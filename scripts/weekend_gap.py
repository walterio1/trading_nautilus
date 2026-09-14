"""
Weekend gap study, written for the E6 continuous contract and usable on any
FirstRate 1-minute futures file (costs are measured per year from the file).

The weekend gap is large relative to costs: on E6 the mean absolute
Friday-close to Sunday-open move is about 0.096% of notional against roughly
0.0056% for a round trip at the real per-year tick, so a sign call only has to
be right a little over half the time to pay for itself. That is a far lower bar than any intraday variant in this project,
where signal and spread are the same order of magnitude.

Entry signal
------------
Fade the move over the last `--entry-window` minutes of Friday: if price rose
into the close, go short over the weekend, and vice versa. This is the same
short-horizon reversion already visible as a -0.021 lag-1 autocorrelation in
the 15-minute bars, captured where holding it costs no extra turnover.

Exit timing
-----------
The default exit is the first bar after the gap. The exit scan also measures
holding past the open, which answers whether the gap keeps going or fills.
Exit horizons are counted in BARS, not wall-clock minutes: bars are not
contiguous across daily breaks, so a bar offset is the honest unit here.

Units are percent of notional per trade, matching the rest of the project.
Absolute price moves are not used anywhere: the input is ratio-adjusted, which
preserves returns but not price differences.

Run
---
    .venv/Scripts/python.exe scripts/weekend_gap.py \
        --data data/E6_full_1min_continuous_ratio_adjusted.txt
"""

import argparse
import statistics
from collections import Counter, defaultdict
from datetime import datetime

ENTRY_HORIZONS = [5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 240, 360]
EXIT_HORIZONS = [0, 15, 30, 60, 120, 240, 480, 960, 1440, 2880]
N_FOLDS = 10


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Weekend gap study.")
    p.add_argument("--data", required=True, help="1-minute CSV, no header")
    p.add_argument("--entry-window", type=int, default=15,
                   help="Minutes of Friday move to fade (default 15)")
    p.add_argument("--min-gap-hours", type=float, default=40.0,
                   help="Minimum gap length counted as a weekend (default 40)")
    return p.parse_args()


def load(path: str) -> list[tuple]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            parts = line.split(",")
            if len(parts) < 5:
                continue
            s = parts[0]
            rows.append((
                datetime(int(s[:4]), int(s[5:7]), int(s[8:10]), int(s[11:13]), int(s[14:16])),
                float(parts[1]), float(parts[4]),
            ))
    return rows


def find_weekends(rows: list[tuple], min_hours: float) -> list[int]:
    """
    Indices of the first bar after each weekend break.

    Filtering on weekday as well as length keeps holiday closures out: the
    daily maintenance break is about an hour so the length test alone already
    excludes it, but the weekday test also rejects mid-week holidays that
    happen to run long.
    """
    out = []
    for i in range(1, len(rows)):
        minutes = (rows[i][0] - rows[i - 1][0]).total_seconds() / 60
        if (minutes > min_hours * 60
                and rows[i - 1][0].weekday() in (3, 4)     # Thursday or Friday
                and rows[i][0].weekday() in (6, 0)):       # Sunday or Monday
            out.append(i)
    return out


def stats(values: list[float]) -> tuple[float, float]:
    """Mean and its t statistic."""
    if len(values) < 2:
        return 0.0, 0.0
    mean = statistics.mean(values)
    sd = statistics.stdev(values)
    return mean, (mean / (sd / len(values) ** 0.5)) if sd else 0.0


def cost_by_year(rows: list[tuple]) -> dict[int, float]:
    """
    Real one-tick cost per year, percent of notional, from the series' own grid.

    The smallest close-to-close increment repeating in at least 1% of bars is
    the tick in adjusted units; dividing by the mean adjusted price cancels the
    ratio-adjustment factor. Same measure as loto_e6.py. It matters here
    because E6's effective tick halved in 2016: a fixed 0.00455% understates
    2008-2015 by 1.5-2x and overstates 2017-2025 slightly.
    """
    inc, pre = defaultdict(list), defaultdict(list)
    for i in range(1, len(rows)):
        y = rows[i][0].year
        if y == rows[i - 1][0].year:
            d = abs(rows[i][2] - rows[i - 1][2])
            if d > 0:
                inc[y].append(round(d, 7))
        pre[y].append(rows[i][2])
    out = {}
    for y, ds in inc.items():
        cand = sorted(v for v, k in Counter(ds).items() if k >= 0.01 * len(ds))
        out[y] = 100.0 * (cand[0] if cand else min(ds)) / (sum(pre[y]) / len(pre[y]))
    return out


def weekend_net(rec: dict, window: int, direction: int, cost: dict[int, float]) -> float | None:
    """
    Net percent for one weekend under (window, direction), or None if no trade.

    direction -1 fades the Friday move, +1 follows it. A Friday window with no
    move at all carries no signal, so it is not traded and pays no cost.
    """
    move = rec[f"pre{window}"]
    if move == 0:
        return None
    return direction * (1 if move > 0 else -1) * rec["gap"] - cost[rec["date"].year]


def validate(records: list[dict], cost: dict[int, float]) -> None:
    """
    Choose the entry window AND the direction without looking at the weekends
    they are scored on, two ways:

      LOTO       10 blocks; each block is traded with the rule chosen on the
                 other nine. Close to out-of-sample, with a small look-ahead:
                 the training set includes weekends after the tested block.
      EXPANDING  train on 0-50%, trade 50-60%; train on 0-60%, trade 60-70%...
                 Strictly out-of-sample, no weekend after the tested block used.

    The direction is free too, because "fade" was itself found by looking at
    the whole sample. Two in-sample objectives, since picking the objective is
    one more choice the procedure does not validate.
    """
    rules = [(w, d) for w in ENTRY_HORIZONS for d in (-1, 1)]
    n = len(records)
    edges = [n * k // N_FOLDS for k in range(N_FOLDS + 1)]
    years = (records[-1]["date"] - records[0]["date"]).days / 365.25

    def scored(idx, rule):
        return [x for x in (weekend_net(records[i], rule[0], rule[1], cost) for i in idx)
                if x is not None]

    def choose(train_idx, objective):
        best, best_score = None, None
        for rule in rules:
            v = scored(train_idx, rule)
            if len(v) < 50:
                continue
            if objective == "net_total":
                score = sum(v)
            else:
                sd = statistics.stdev(v)
                score = statistics.mean(v) / (sd / len(v) ** 0.5) if sd else 0.0
            if best_score is None or score > best_score:
                best, best_score = rule, score
        return best

    def label(rule):
        return f"{'fade' if rule[1] == -1 else 'follow'} {rule[0]} min"

    fixed = [x for x in (weekend_net(r, 15, -1, cost) for r in records) if x is not None]
    mf, tf = stats(fixed)
    half = len(fixed) // 2
    print(f"\nCONFIRMATION with the real cost per year (fade 15 min, exit at the open, "
          f"no trade when Friday did not move):")
    print(f"  {len(fixed)} trades, {mf:+.5f}% per weekend, t {tf:+.2f}, "
          f"total {sum(fixed):+.2f}% = {sum(fixed) / years:+.3f}%/year, "
          f"halves {sum(fixed[:half]):+.2f}% / {sum(fixed[half:]):+.2f}%")

    yearly: dict[str, dict[int, list[float]]] = {}
    for objective in ("net_total", "t"):
        print(f"\nLOTO - window and direction chosen on the other 90%, objective = {objective}")
        print(f"  {'fold':<6}{'dates':<25}{'chosen':<18}{'trades':>7}{'net %':>9}")
        total, trades, positive, chosen, oos = 0.0, 0, 0, [], {}
        for f in range(N_FOLDS):
            test = range(edges[f], edges[f + 1])
            train = [i for i in range(n) if i < edges[f] or i >= edges[f + 1]]
            rule = choose(train, objective)
            v = scored(test, rule)
            total += sum(v)
            trades += len(v)
            positive += sum(v) > 0
            chosen.append(rule)
            for i in test:
                x = weekend_net(records[i], rule[0], rule[1], cost)
                if x is not None:
                    oos.setdefault(records[i]["date"].year, []).append(x)
            dates = f"{records[edges[f]]['date']:%Y-%m}..{records[edges[f + 1] - 1]['date']:%Y-%m}"
            print(f"  {f + 1:<6}{dates:<25}{label(rule):<18}{len(v):>7}{sum(v):>+9.2f}")
        yearly[objective] = oos
        yp = sum(1 for y in oos if sum(oos[y]) > 0)
        print(f"  LOTO: {total:+.2f}% over {years:.1f} years = {total / years:+.3f}%/year, "
              f"{trades} trades, {total / trades:+.5f}% per trade, {positive}/{N_FOLDS} folds "
              f"positive, {yp}/{len(oos)} years positive")
        print("  chosen: " + " · ".join(f"{c}/{N_FOLDS} {label(r)}"
                                         for r, c in Counter(chosen).most_common()))

        total, positive, picks = 0.0, 0, []
        for k in range(N_FOLDS // 2, N_FOLDS):
            rule = choose(range(edges[k]), objective)
            v = scored(range(edges[k], edges[k + 1]), rule)
            total += sum(v)
            positive += sum(v) > 0
            picks.append(f"{label(rule)} {sum(v):+.2f}%")
        span = (records[-1]["date"] - records[edges[N_FOLDS // 2]]["date"]).days / 365.25
        print(f"  EXPANDING window (stricter): {total:+.2f}% over the last {span:.1f} years = "
              f"{total / span:+.3f}%/year, {positive}/{N_FOLDS - N_FOLDS // 2} blocks positive"
              f"   [{' | '.join(picks)}]")

    # Year by year: every weekend is scored by a rule chosen WITHOUT its own
    # tenth, so this is the stability of the validated model, not of a fit.
    fixed_by_year: dict[int, list[float]] = {}
    for r in records:
        x = weekend_net(r, 15, -1, cost)
        if x is not None:
            fixed_by_year.setdefault(r["date"].year, []).append(x)
    print(f"\nYEAR BY YEAR, net % of notional after real cost")
    print(f"  {'year':<6}{'cost %':>8}{'trades':>8}{'LOTO net_total':>16}{'LOTO t':>10}"
          f"{'fixed 15 min':>14}")
    for y in sorted(fixed_by_year):
        a = sum(yearly["net_total"].get(y, []))
        b = sum(yearly["t"].get(y, []))
        c = sum(fixed_by_year[y])
        print(f"  {y:<6}{cost[y]:>8.5f}{len(yearly['net_total'].get(y, [])):>8}"
              f"{a:>+16.2f}{b:>+10.2f}{c:>+14.2f}")
    for name, series in (("LOTO net_total", yearly["net_total"]), ("LOTO t", yearly["t"]),
                         ("fixed 15 min", fixed_by_year)):
        vals = [sum(v) for v in series.values()]
        top2 = sorted(vals, reverse=True)[:2]
        print(f"  {name:<15} {sum(1 for v in vals if v > 0)}/{len(vals)} years positive, "
              f"median year {statistics.median(vals):+.2f}%, worst {min(vals):+.2f}%, "
              f"best two years = {100 * sum(top2) / sum(vals):.0f}% of the total")


def ensembles(records: list[dict], cost: dict[int, float]) -> None:
    """
    Replace the single best window with a combination of windows.

    The single-window LOTO loses most of its edge on WHICH plateau window it
    picks per fold (fold 1 picked 60 min and lost -3.71%). Combining windows
    removes that choice. To avoid sneaking a new in-sample choice back in, the
    combination is either fixed a priori over ALL windows, or selected inside
    each training set:

      vote_all      position = sign of the sum of fade votes over all windows
      mean_all      position = mean of the fade votes, a fraction in [-1, 1]:
                    bigger when the windows agree, cost scaled by |position|
      mean_pos_in   mean over the windows whose fade net is positive on the
                    training 90% of that fold
      mean_top5_in  mean over the five best windows on the training 90%

    Fade is kept as the direction: the single-window LOTO chose it in 10/10
    folds, so leaving it free would change nothing.
    """
    n = len(records)
    edges = [n * k // N_FOLDS for k in range(N_FOLDS + 1)]
    years = (records[-1]["date"] - records[0]["date"]).days / 365.25
    ys = sorted({r["date"].year for r in records})

    def vote(rec, w):
        m = rec[f"pre{w}"]
        return -1 if m > 0 else (1 if m < 0 else 0)

    def net(rec, windows, mode):
        v = [vote(rec, w) for w in windows]
        pos = (1 if sum(v) > 0 else (-1 if sum(v) < 0 else 0)) if mode == "vote" else sum(v) / len(v)
        if pos == 0:
            return None
        return pos * rec["gap"] - abs(pos) * cost[rec["date"].year]

    def window_net(idx, w):
        return sum(x for x in (net(records[i], [w], "vote") for i in idx) if x is not None)

    def windows_for(train, variant):
        if variant in ("vote_all", "mean_all"):
            return ENTRY_HORIZONS
        ranked = sorted(ENTRY_HORIZONS, key=lambda w: -window_net(train, w))
        if variant == "mean_pos_in":
            chosen = [w for w in ranked if window_net(train, w) > 0]
            return chosen or ranked[:1]
        return ranked[:5]

    def single_best(train):
        return [max(ENTRY_HORIZONS, key=lambda w: window_net(train, w))]

    variants = {
        "single best (LOTO)": (lambda tr: single_best(tr), "vote"),
        "vote_all": (lambda tr: windows_for(tr, "vote_all"), "vote"),
        "mean_all": (lambda tr: windows_for(tr, "mean_all"), "mean"),
        "mean_pos_in": (lambda tr: windows_for(tr, "mean_pos_in"), "mean"),
        "mean_top5_in": (lambda tr: windows_for(tr, "mean_top5_in"), "mean"),
    }

    print(f"\n{'#' * 100}\nENSEMBLES OF WINDOWS - LOTO, fade, real cost\n{'#' * 100}")
    print(f"  {'variant':<20}{'%/year':>8}{'folds+':>8}{'years+':>8}{'median yr':>10}"
          f"{'worst yr':>10}{'top2 share':>11}{'exp. %/yr':>10}{'blocks+':>8}")
    yearly_all = {}
    for name, (pick, mode) in variants.items():
        by_year: dict[int, float] = defaultdict(float)
        total, folds_pos = 0.0, 0
        for f in range(N_FOLDS):
            train = [i for i in range(n) if i < edges[f] or i >= edges[f + 1]]
            windows = pick(train)
            fold_net = 0.0
            for i in range(edges[f], edges[f + 1]):
                x = net(records[i], windows, mode)
                if x is not None:
                    fold_net += x
                    by_year[records[i]["date"].year] += x
            total += fold_net
            folds_pos += fold_net > 0
        exp_total, exp_pos = 0.0, 0
        for k in range(N_FOLDS // 2, N_FOLDS):
            windows = pick(list(range(edges[k])))
            block = sum(x for x in (net(records[i], windows, mode)
                                    for i in range(edges[k], edges[k + 1])) if x is not None)
            exp_total += block
            exp_pos += block > 0
        span = (records[-1]["date"] - records[edges[N_FOLDS // 2]]["date"]).days / 365.25
        vals = [by_year[y] for y in ys]
        top2 = sorted(vals, reverse=True)[:2]
        share = f"{100 * sum(top2) / total:.0f}%" if total > 0 else "n/a"
        yearly_all[name] = by_year
        print(f"  {name:<20}{total / years:>+8.3f}{folds_pos:>5}/{N_FOLDS}"
              f"{sum(1 for v in vals if v > 0):>5}/{len(ys)}{statistics.median(vals):>+10.2f}"
              f"{min(vals):>+10.2f}{share:>11}{exp_total / span:>+10.3f}{exp_pos:>5}/5")

    print(f"\n  YEAR BY YEAR (net %)")
    print("  " + f"{'year':<6}" + "".join(f"{name[:18]:>20}" for name in variants))
    for y in ys:
        print("  " + f"{y:<6}" + "".join(f"{yearly_all[name][y]:>+20.2f}" for name in variants))


def correlation(pairs: list[tuple]) -> float:
    mx = statistics.mean([a for a, _ in pairs])
    my = statistics.mean([b for _, b in pairs])
    num = sum((a - mx) * (b - my) for a, b in pairs)
    den = (sum((a - mx) ** 2 for a, _ in pairs) * sum((b - my) ** 2 for _, b in pairs)) ** 0.5
    return num / den if den else 0.0


def main() -> None:
    args = parse_args()
    rows = load(args.data)
    weekends = find_weekends(rows, args.min_gap_hours)
    print(f"{len(rows)} one-minute bars, {len(weekends)} weekend gaps")

    records = []
    for i in weekends:
        pre_close = rows[i - 1][2]
        rec = {"date": rows[i - 1][0], "gap": (rows[i][1] / pre_close - 1) * 100}
        ok = True
        for w in ENTRY_HORIZONS:
            j = i - 1 - w
            if j < 0:
                ok = False
                break
            rec[f"pre{w}"] = (pre_close / rows[j][2] - 1) * 100
        for h in EXIT_HORIZONS:
            k = i + h
            rec[f"exit{h}"] = (rows[k][2] / pre_close - 1) * 100 if k < len(rows) else None
        if ok:
            records.append(rec)

    # Real one-tick cost per year for whichever instrument is loaded, so the
    # script works on ES as well as E6; `cost_mean` is only for headline ratios.
    cost = cost_by_year(rows)
    cost_mean = statistics.mean(cost[r["date"].year] for r in records)
    print(f"real one-tick cost: mean {cost_mean:.5f}% of notional, "
          f"{min(cost.values()):.5f}%-{max(cost.values()):.5f}% by year")

    gaps = [r["gap"] for r in records]
    absmean = statistics.mean([abs(g) for g in gaps])
    mean, t = stats(gaps)
    print("\nGap, Friday close to Sunday open:")
    print(f"  mean {mean:+.5f}%  t {t:+.2f}   median {statistics.median(gaps):+.5f}%")
    print(f"  mean |gap| {absmean:.5f}%  = {absmean / cost_mean:.1f}x a round trip")
    print(f"  sign accuracy needed to cover cost: {50 * (1 + cost_mean / absmean):.1f}%")

    half = len(records) // 2
    print("\nENTRY SCAN - fade the last N minutes of Friday, exit at the open")
    header = (f"{'window':>9}{'total %':>11}{'t':>7}{'1st half':>11}{'t':>7}"
              f"{'2nd half':>11}{'t':>7}{'hit':>8}{'x cost':>9}")
    print(header)
    print("-" * len(header))
    for w in ENTRY_HORIZONS:
        v = [(-1 if r[f"pre{w}"] > 0 else 1) * r["gap"] for r in records]
        mt, tt = stats(v)
        ma, ta = stats(v[:half])
        mb, tb = stats(v[half:])
        hit = 100 * sum(1 for x in v if x > 0) / len(v)
        print(f"{w:>7} m{mt:>+11.5f}{tt:>+7.2f}{ma:>+11.5f}{ta:>+7.2f}{mb:>+11.5f}{tb:>+7.2f}"
              f"{hit:>7.1f}%{mt / cost_mean:>8.2f}x")

    w = args.entry_window
    print(f"\nEXIT SCAN - entry fades the last {w} minutes; exit N bars after the open")
    print(header)
    print("-" * len(header))
    for h in EXIT_HORIZONS:
        pairs = [(r, r[f"exit{h}"]) for r in records if r[f"exit{h}"] is not None]
        v = [(-1 if r[f"pre{w}"] > 0 else 1) * e for r, e in pairs]
        mt, tt = stats(v)
        ma, ta = stats(v[:len(v) // 2])
        mb, tb = stats(v[len(v) // 2:])
        hit = 100 * sum(1 for x in v if x > 0) / len(v)
        lab = "open" if h == 0 else f"+{h}"
        print(f"{lab:>9}{mt:>+11.5f}{tt:>+7.2f}{ma:>+11.5f}{ta:>+7.2f}{mb:>+11.5f}{tb:>+7.2f}"
              f"{hit:>7.1f}%{mt / cost_mean:>8.2f}x")

    print("\nGAP CONTINUATION - correlation between the gap and the move after the open")
    print("  negative means the gap fills, positive means it keeps going")
    for h in EXIT_HORIZONS[1:]:
        pairs = [(r["gap"], r[f"exit{h}"] - r["gap"]) for r in records if r[f"exit{h}"] is not None]
        c = correlation(pairs)
        print(f"  +{h:>5} bars: corr {c:+.4f}   {'fills' if c < 0 else 'continues'}")

    by_year: dict[int, list[float]] = {}
    for r in records:
        by_year.setdefault(r["date"].year, []).append((-1 if r[f"pre{w}"] > 0 else 1) * r["gap"])
    positive = sum(1 for y in by_year if statistics.mean(by_year[y]) > 0)
    years = (records[-1]["date"] - records[0]["date"]).days / 365.25
    net = statistics.mean([(-1 if r[f"pre{w}"] > 0 else 1) * r["gap"] - cost[r["date"].year]
                           for r in records])
    print(f"\nYEARLY, fading the last {w} minutes and exiting at the open:")
    print(f"  {positive} of {len(by_year)} years positive")
    print(f"  net after cost {net:+.5f}% per weekend, "
          f"about {net * len(records) / years:+.2f}% per year on notional")

    validate(records, cost)
    ensembles(records, cost)


if __name__ == "__main__":
    main()
