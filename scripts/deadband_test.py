"""
Compare candidate endogenous deadbands for the crossover.

The signal is sign(fast - slow); a deadband suppresses trading while the two
MAs are too close to be informative, which is where most of the turnover (and
therefore most of the spread bill) is spent.

The band has to live on the same quantity as the signal, |fast - slow|. That
rules out anything built from price increments alone: the difference of two
means is damped relative to the price by roughly 1/sqrt(N), so converting a
price sigma into a band on the difference needs a multiplier, which is exactly
the exogenous parameter this design avoids. Worse, |fast - slow| scales with
BOTH the price volatility and the two window lengths, and those move every bar,
so a fixed multiple of price sigma is mis-scaled whenever N moves.

Candidates measured here, all parameter-free once N is endogenous:

  none        trade on every crossover (the current system)
  mean_nf     band = mean |diff| over the last N_fast bars
  mean_ns     band = mean |diff| over the last N_slow bars
  sd_ns       band = stdev of diff over the last N_slow bars
  price_ns    band = mean |price increment| over the last N_slow bars
              (the wrong-scale candidate, included to show what it does)
  agree       no band: trade only when fast-slow and fast-super agree in sign

Accounting is in percent of notional. Turnover is charged half a tick per unit
of position change, so a reversal (2 units) pays a full tick and an exit to
flat (1 unit) pays half - which is what makes going flat cheaper than flipping.
"""

import argparse
import csv
import statistics
from collections import deque

TICK = 0.00005
REF_PRICE = 1.10
HALF_TICK_PCT = TICK / REF_PRICE * 100 / 2  # cost per unit of position change


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare endogenous deadbands.")
    parser.add_argument("--pnl", required=True, help="Per-bar series from pnl_series.py")
    return parser.parse_args()


def run(diffs, agree, bands, fills, name):
    """
    Replay reversion trading with one band series. `bands[i]` is None where the
    band is undefined, which suppresses trading rather than allowing it.
    """
    position = 0
    gross = 0.0
    turnover = 0.0
    trades = 0
    bars_in = 0

    for i in range(len(diffs) - 1):
        d = diffs[i]
        band = bands[i] if bands is not None else 0.0
        if d is None or band is None:
            target = position
        elif name == "agree":
            target = -1 if d > 0 else (1 if d < 0 else 0)
            if not agree[i]:
                target = 0
        elif abs(d) > band:
            target = -1 if d > 0 else 1        # reversion: fade the crossover
        else:
            target = 0

        if target != position:
            turnover += abs(target - position) * HALF_TICK_PCT
            if target != 0:
                trades += 1
            position = target

        if position != 0 and fills[i] and fills[i + 1]:
            gross += position * (fills[i + 1] / fills[i] - 1) * 100.0
            bars_in += 1

    return {
        "name": name, "gross": gross, "cost": turnover, "net": gross - turnover,
        "trades": trades, "bars_in": bars_in,
        "per_trade": gross / trades if trades else 0.0,
    }


def trailing(values, windows, fn):
    """Apply `fn` to the trailing `windows[i]` values ending at i-1."""
    out = []
    buf = deque(maxlen=4096)
    for i, v in enumerate(values):
        n = windows[i]
        if n and len(buf) >= n and n >= 2:
            out.append(fn(list(buf)[-n:]))
        else:
            out.append(None)
        buf.append(v if v is not None else 0.0)
    return out


def main() -> None:
    args = parse_args()
    diffs, absd, fills, nf, ns, agree, incr = [], [], [], [], [], [], []
    prev_price = None
    for row in csv.DictReader(open(args.pnl, newline="", encoding="utf-8")):
        f, s, sup = row["fast_ma"], row["slow_ma"], row["super_slow_ma"]
        if f and s and sup:
            d = float(f) - float(s)
            diffs.append(d)
            agree.append((d > 0) == (float(f) - float(sup) > 0))
        else:
            diffs.append(None)
            agree.append(False)
        absd.append(abs(diffs[-1]) if diffs[-1] is not None else None)
        fills.append(float(row["fill_price"]))
        nf.append(int(row["n_fast"]) if row["n_fast"] else 0)
        ns.append(int(row["n_slow"]) if row["n_slow"] else 0)
        price = float(row["close"])
        incr.append(abs(price - prev_price) if prev_price is not None else 0.0)
        prev_price = price

    print(f"{len(diffs)} bars from {args.pnl}")

    # Does |diff| really move with N? If so, a band scaled off price sigma alone
    # is mis-calibrated whenever the endogenous window shifts.
    by_n: dict[int, list[float]] = {}
    for i, d in enumerate(absd):
        if d is not None and ns[i]:
            by_n.setdefault(ns[i], []).append(d)
    print("\nMedia de |fast-slow| segun N_slow (si crece con N, escalar por sigma del precio falla):")
    for n in sorted(by_n)[:8]:
        vals = by_n[n]
        if len(vals) > 200:
            print(f"   N_slow={n:>3}: {statistics.mean(vals):.6f}   ({len(vals)} barras)")

    variants = {
        "none": None,
        "mean_nf": trailing(absd, nf, lambda w: sum(w) / len(w)),
        "mean_ns": trailing(absd, ns, lambda w: sum(w) / len(w)),
        "sd_ns": trailing(diffs, ns, lambda w: statistics.pstdev(w)),
        "price_ns": trailing(incr, ns, lambda w: sum(w) / len(w)),
        "agree": None,
    }

    print(f"\n{'banda':<11}{'ops':>8}{'barras dentro':>15}{'bruto %':>10}"
          f"{'coste %':>10}{'neto %':>10}{'% / op':>10}{'vs coste':>10}")
    print("-" * 84)
    cost_pct = TICK / REF_PRICE * 100
    for name, bands in variants.items():
        r = run(diffs, agree, bands, fills, name)
        print(f"{name:<11}{r['trades']:>8}{r['bars_in']:>15}{r['gross']:>10.2f}"
              f"{r['cost']:>10.2f}{r['net']:>10.2f}{r['per_trade']:>10.5f}"
              f"{r['per_trade'] / cost_pct:>10.2f}x")


if __name__ == "__main__":
    main()
