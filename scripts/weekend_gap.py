"""
Weekend gap study for the E6 continuous contract.

The weekend gap is large relative to costs: the mean absolute Friday-close to
Sunday-open move is about 0.096% of notional against roughly 0.00455% for a
round trip, so a sign call only has to be right about 52.4% of the time to pay
for itself. That is a far lower bar than any intraday variant in this project,
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
from datetime import datetime

COST_PCT = 0.00455          # one tick round trip at EUR/USD 1.10
ENTRY_HORIZONS = [5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 240, 360]
EXIT_HORIZONS = [0, 15, 30, 60, 120, 240, 480, 960, 1440, 2880]


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

    gaps = [r["gap"] for r in records]
    absmean = statistics.mean([abs(g) for g in gaps])
    mean, t = stats(gaps)
    print("\nGap, Friday close to Sunday open:")
    print(f"  mean {mean:+.5f}%  t {t:+.2f}   median {statistics.median(gaps):+.5f}%")
    print(f"  mean |gap| {absmean:.5f}%  = {absmean / COST_PCT:.1f}x a round trip")
    print(f"  sign accuracy needed to cover cost: {50 * (1 + COST_PCT / absmean):.1f}%")

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
              f"{hit:>7.1f}%{mt / COST_PCT:>8.2f}x")

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
              f"{hit:>7.1f}%{mt / COST_PCT:>8.2f}x")

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
    net = statistics.mean([(-1 if r[f"pre{w}"] > 0 else 1) * r["gap"] for r in records]) - COST_PCT
    print(f"\nYEARLY, fading the last {w} minutes and exiting at the open:")
    print(f"  {positive} of {len(by_year)} years positive")
    print(f"  net after cost {net:+.5f}% per weekend, "
          f"about {net * len(records) / years:+.2f}% per year on notional")


if __name__ == "__main__":
    main()
