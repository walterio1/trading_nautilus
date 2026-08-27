"""
Per-bar PnL series for the three crossover pairings, ready for the regime layer.

Produces one row per bar with, for each pairing, the position held, the return
earned over that bar, and the running cumulative profit in percent:

    A = fast + slow
    B = fast + superSlow
    C = slow + superSlow

Direction
---------
Series are generated in the TREND direction: long while the pairing's faster
leg is above its slower leg. The sign of an increment therefore reads directly
as "trend paid over this stretch" (+) or "reversion paid" (-), which is exactly
the discriminator the regime layer needs. Reversion PnL is the negation, so
nothing is lost by fixing the direction here.

Return definition
-----------------
The position held for bar t is decided by that bar's close and executed at
`fill_price` (the close of the next available one-minute bar), so the return
actually capturable is

    ret_t = position_t * (fill_{t+1} / fill_t - 1)

Cumulative profit is the running SUM of those returns in percentage points, not
a compounded figure, matching how the regime increments were specified.

No costs are applied. Holding a position across bars and closing/reopening it
at the same price are identical without costs, so the series is unaffected by
whether a nominal trade occurs on a given bar.

Flat crossovers
---------------
When the two legs are exactly equal the crossover has no sign and the live
strategy leaves the position untouched, so the previous position is carried
forward here too rather than being flattened.

Segments
--------
`seg_id` increments on every slow-superSlow crossover, the reset event for the
regime definition; `seg_bar` counts bars within the segment. Both are written
out so the regime layer can segment without recomputing the moving averages.

Run
---
    .venv\\Scripts\\python.exe scripts\\pnl_series.py \\
        --csv data\\E6_15min.csv --slow-mode smoothed_runs \\
        --output data\\E6_pnl_smoothed_runs.csv
"""

import argparse
import csv
import os
import statistics
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ibkr_paper_ma_crossover import SLOW_MA_BUILDERS  # noqa: E402
from ibkr_paper_ma_crossover import LaggedWindowMovingAverage  # noqa: E402
from ibkr_paper_ma_crossover import RunLengthMovingAverage  # noqa: E402

from types import SimpleNamespace  # noqa: E402


PAIRINGS = (("a", "fast", "slow"), ("b", "fast", "super"), ("c", "slow", "super"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build per-bar PnL series for the three crossover pairings.",
    )
    parser.add_argument("--csv", required=True, help="Bars CSV from prepare_bars.py")
    parser.add_argument("--output", required=True, help="Destination CSV")
    parser.add_argument("--slow-mode", default="smoothed_runs",
                        choices=sorted(SLOW_MA_BUILDERS),
                        help="How the slow MA derives its window")
    parser.add_argument("--levels", type=int, default=3,
                        help="superSlow depth (default 3)")
    parser.add_argument("--fill-col", default="fill_price",
                        help="Column holding the execution price")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    stamps: list[str] = []
    closes: list[Decimal] = []
    fills: list[Decimal] = []
    with open(args.csv, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            stamps.append(row["timestamp"])
            closes.append(Decimal(row["close"]))
            fills.append(Decimal(row[args.fill_col]))
    print(f"Loaded {len(closes)} bars from {args.csv}")
    print(f"  slow mode  : {args.slow_mode}")
    print(f"  superSlow  : levels={args.levels}")

    fast = RunLengthMovingAverage()
    slow = SLOW_MA_BUILDERS[args.slow_mode](SimpleNamespace(super_slow_levels=args.levels))
    super_slow = LaggedWindowMovingAverage(levels=args.levels)

    # Positions persist across flat crossovers, so they live outside the loop.
    positions = {key: 0 for key, _, _ in PAIRINGS}
    cumulative = {key: 0.0 for key, _, _ in PAIRINGS}
    returns: dict[str, list[float]] = {key: [] for key, _, _ in PAIRINGS}

    seg_id = 0
    seg_bar = 0
    prev_seg_sign: int | None = None
    seg_lengths: list[int] = []

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    out = open(args.output, "w", newline="", encoding="utf-8")
    writer = csv.writer(out)
    writer.writerow([
        "timestamp", "close", "fill_price",
        "fast_ma", "slow_ma", "super_slow_ma", "n_fast", "n_slow", "n_super",
        "pos_a", "pos_b", "pos_c",
        "ret_a", "ret_b", "ret_c",
        "cum_a", "cum_b", "cum_c",
        "seg_id", "seg_bar",
    ])

    n = len(closes)
    for i in range(n):
        price = closes[i]
        fast.update_raw(price)
        slow.update_raw(price, fast.period)
        super_slow.update_raw(price, fast.period)

        values = {"fast": fast, "slow": slow, "super": super_slow}

        # Position for this bar, from this bar's close. Carried forward when a
        # pairing is flat or either leg is still warming up.
        for key, faster, slower in PAIRINGS:
            f_ma, s_ma = values[faster], values[slower]
            if f_ma.initialized and s_ma.initialized:
                diff = f_ma.value - s_ma.value
                if diff > 0:
                    positions[key] = 1
                elif diff < 0:
                    positions[key] = -1

        # Segment bookkeeping: the slow-superSlow crossover is the reset event.
        if slow.initialized and super_slow.initialized:
            d = slow.value - super_slow.value
            sign = 1 if d > 0 else (-1 if d < 0 else 0)
            if sign != 0:
                if prev_seg_sign is not None and sign != prev_seg_sign:
                    seg_lengths.append(seg_bar)
                    seg_id += 1
                    seg_bar = 0
                prev_seg_sign = sign
        seg_bar += 1

        # The return is earned between this bar's fill and the next one's, so
        # the final bar has no return to attribute.
        bar_returns = {}
        if i + 1 < n and fills[i] != 0:
            move = float(fills[i + 1] / fills[i] - 1) * 100.0
            for key, _, _ in PAIRINGS:
                r = positions[key] * move
                bar_returns[key] = r
                cumulative[key] += r
                if positions[key] != 0:
                    returns[key].append(r)
        else:
            bar_returns = {key: 0.0 for key, _, _ in PAIRINGS}

        writer.writerow([
            stamps[i], closes[i], fills[i],
            f"{fast.value:.6f}" if fast.initialized else "",
            f"{slow.value:.6f}" if slow.initialized else "",
            f"{super_slow.value:.6f}" if super_slow.initialized else "",
            fast.period if fast.initialized else "",
            slow.period if slow.initialized else "",
            super_slow.period if super_slow.initialized else "",
            positions["a"], positions["b"], positions["c"],
            f"{bar_returns['a']:.8f}", f"{bar_returns['b']:.8f}", f"{bar_returns['c']:.8f}",
            f"{cumulative['a']:.6f}", f"{cumulative['b']:.6f}", f"{cumulative['c']:.6f}",
            seg_id, seg_bar,
        ])

    out.close()
    print(f"Wrote {n} rows to {args.output}")
    print()
    print("Cumulative profit in TREND direction (reversion is the negation):")
    print(f"{'serie':<28}{'cum %':>12}{'barras':>10}{'% barras +':>12}{'desv/barra':>12}")
    print("-" * 74)
    labels = {"a": "A = fast+slow", "b": "B = fast+superSlow", "c": "C = slow+superSlow"}
    for key, _, _ in PAIRINGS:
        series = returns[key]
        pos_share = 100.0 * sum(1 for r in series if r > 0) / len(series) if series else 0.0
        sd = statistics.stdev(series) if len(series) > 1 else 0.0
        print(f"{labels[key]:<28}{cumulative[key]:>12.2f}{len(series):>10}"
              f"{pos_share:>12.1f}{sd:>12.4f}")

    print()
    print(f"Segments (slow-superSlow crossovers): {len(seg_lengths)}")
    if seg_lengths:
        print(f"  mean {statistics.mean(seg_lengths):.1f} bars, "
              f"median {statistics.median(seg_lengths):.0f}")


if __name__ == "__main__":
    main()
