"""
Out-of-sample validation of the regime layer.

Splits the history in two chronological halves, selects the profitable
regime/operativa cells on the FIRST half only, then measures exactly those
cells on the SECOND half. A rule that only works on the half it was chosen
from is a fitting artifact, not an edge.

Everything is reported as reversion edge PER TRANSACTION, in percent of
notional. Two reasons for those units:

  percent  - the input is a ratio-adjusted continuous contract, which preserves
             returns but NOT absolute price moves. Converting a price
             difference to dollars would overstate the early years, where the
             adjustment factor scales prices up. Percent is the only figure
             that is comparable across the whole history.
  per trade - the spread is paid when a position turns over, not on every bar,
             so per-bar figures understate the edge by the average holding
             period (about 15 bars here) and cannot be compared to the cost.

COST_PCT below is one tick crossed on a reversal, expressed as a fraction of
notional. Note it drifts with the price level - one tick is 0.0031% of notional
with the euro at 1.60 and 0.0053% at 0.95 - so treat it as a central estimate,
not a constant.

Reversion is the negation of the trend-direction PnL in the input series, so a
positive number here means "trading this pairing against its crossover made
money in this regime".

Run
---
    .venv\\Scripts\\python.exe scripts\\regime_oos.py \\
        --pnl data\\E6_pnl_lagged_fast.csv
"""

import argparse
import csv
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from regimes import classify  # noqa: E402

ORDER = ["+++", "++-", "+-+", "-++", "+--", "-+-", "--+", "---"]
PAIRINGS = ("a", "b", "c")
CONTRACT = 125000      # 1 E6 contract, in EUR
TICK = 0.00005         # E6 minimum price increment
REF_PRICE = 1.10       # Central EUR/USD level for expressing the tick in percent
COST_PCT = TICK / REF_PRICE * 100  # ~0.00455% of notional per reversal


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Out-of-sample test of the regime layer.")
    parser.add_argument("--pnl", required=True, help="Per-bar series from pnl_series.py")
    parser.add_argument("--threshold", type=float, default=COST_PCT,
                        help="Percent per trade a cell must clear in-sample to be "
                             f"selected (default {COST_PCT:.5f}, one tick)")
    parser.add_argument("--reverse", action="store_true",
                        help="Select on the SECOND half and validate on the FIRST. Run "
                             "both ways to tell selection bias from a genuine decay over "
                             "time: bias shrinks the result in both directions, decay "
                             "only when validating on the later half.")
    return parser.parse_args()


def load_segments(path: str) -> list[dict]:
    """Aggregate the per-bar series into one record per segment."""
    rows = list(csv.DictReader(open(path, newline="", encoding="utf-8")))
    segments: dict[int, dict] = {}
    previous = {k: 0 for k in PAIRINGS}

    for i, row in enumerate(rows):
        sid = int(row["seg_id"])
        seg = segments.setdefault(sid, {
            "stamp": row["timestamp"], "bars": 0,
            **{f"pct_{k}": 0.0 for k in PAIRINGS},
            **{f"usd_{k}": 0.0 for k in PAIRINGS},
            **{f"ops_{k}": 0 for k in PAIRINGS},
        })
        fill = float(row["fill_price"])
        next_fill = float(rows[i + 1]["fill_price"]) if i + 1 < len(rows) else fill
        for k in PAIRINGS:
            pos = int(row[f"pos_{k}"] or 0)
            seg[f"pct_{k}"] += float(row[f"ret_{k}"])
            seg[f"usd_{k}"] += pos * (next_fill - fill) * CONTRACT
            # A trade is a change into a non-flat position, matching how the
            # live strategy reverses; flat carry-forward is not a trade.
            if pos != previous[k] and pos != 0:
                seg[f"ops_{k}"] += 1
            previous[k] = pos
        seg["bars"] += 1

    ids = sorted(segments)[1:-1]  # drop warm-up segment 0 and the incomplete tail
    return [segments[i] for i in ids]


def bucket(segments: list[dict], lo: int, hi: int) -> dict[str, dict]:
    """
    Accumulate segments [lo, hi) by the regime of the PRECEDING segment.

    The regime always comes from segments[j-1], including when j == lo, so the
    two halves are scored the same way and no lookahead crosses the split.
    """
    out = {code: {f"pct_{k}": 0.0 for k in PAIRINGS} | {f"ops_{k}": 0 for k in PAIRINGS}
           | {"segs": 0, "bars": 0} for code in ORDER}
    for j in range(max(lo, 1), hi):
        prev, cur = segments[j - 1], segments[j]
        regime = classify(prev["pct_a"], prev["pct_b"], prev["pct_c"])
        if regime is None:
            continue
        cell = out[regime.code]
        for k in PAIRINGS:
            cell[f"pct_{k}"] += cur[f"pct_{k}"]
            cell[f"ops_{k}"] += cur[f"ops_{k}"]
        cell["segs"] += 1
        cell["bars"] += cur["bars"]
    return out


def edge_pct(cell: dict, k: str) -> float:
    """Reversion edge per transaction, in percent. Reversion negates the trend PnL."""
    ops = cell[f"ops_{k}"]
    return (-cell[f"pct_{k}"] / ops) if ops else 0.0


def main() -> None:
    args = parse_args()
    segments = load_segments(args.pnl)
    split = len(segments) // 2
    print(f"{len(segments)} segments from {args.pnl}")
    print(f"  in-sample     : segments 0..{split - 1}   {segments[0]['stamp']} .. {segments[split - 1]['stamp']}")
    print(f"  out-of-sample : segments {split}..{len(segments) - 1}   {segments[split]['stamp']} .. {segments[-1]['stamp']}")
    print()

    first = bucket(segments, 0, split)
    second = bucket(segments, split, len(segments))
    ins, oos = (second, first) if args.reverse else (first, second)
    if args.reverse:
        print("REVERSED: selecting on the second half, validating on the first.")
        print()

    # Unconditional baseline: every cell, no regime filter at all. If this alone
    # differs between halves, part of any shrinkage is the period, not fitting.
    for label, half in (("first half ", first), ("second half", second)):
        pct = sum(-half[c][f"pct_{k}"] for c in ORDER for k in PAIRINGS)
        ops = sum(half[c][f"ops_{k}"] for c in ORDER for k in PAIRINGS)
        print(f"Baseline, no regime filter, {label}: {ops:>7} trades, "
              f"{pct / ops:>9.5f}% /trade ({pct / ops / COST_PCT:>5.2f}x cost), "
              f"{pct:>9.2f}% total")
    print()

    print(f"Reversion edge per TRANSACTION, in percent of notional "
          f"(one tick costs about {COST_PCT:.5f}%)")
    print(f"{'regime':<8}{'IS segs':>8}{'A is':>8}{'A oos':>8}{'B is':>8}{'B oos':>8}"
          f"{'C is':>8}{'C oos':>8}   selected in-sample")
    print("-" * 96)
    selected: list[tuple[str, str]] = []
    for code in ORDER:
        cells = ""
        picks = []
        for k in PAIRINGS:
            e_is, e_oos = edge_pct(ins[code], k), edge_pct(oos[code], k)
            cells += f"{e_is:>8.5f}{e_oos:>8.5f}"
            if e_is > args.threshold and ins[code][f"ops_{k}"] > 0:
                picks.append(k.upper())
                selected.append((code, k))
        print(f"{code:<8}{ins[code]['segs']:>8}{cells}   {','.join(picks) or '-'}")

    print()
    if not selected:
        print(f"No cell cleared {args.threshold:.5f}% in-sample.")
        return

    total_pct = sum(-oos[c][f"pct_{k}"] for c, k in selected)
    total_ops = sum(oos[c][f"ops_{k}"] for c, k in selected)
    is_pct = sum(-ins[c][f"pct_{k}"] for c, k in selected)
    is_ops = sum(ins[c][f"ops_{k}"] for c, k in selected)
    held = [(c, k) for c, k in selected if edge_pct(oos[c], k) > args.threshold]

    print(f"Portfolio of the {len(selected)} cells selected in-sample "
          f"(threshold {args.threshold:.5f}%):")
    print(f"  in-sample     : {is_ops:>7} trades, {is_pct / is_ops:>9.5f}% /trade "
          f"({is_pct / is_ops / COST_PCT:>5.2f}x cost), {is_pct:>9.2f}% total")
    oos_per = total_pct / total_ops if total_ops else 0.0
    print(f"  out-of-sample : {total_ops:>7} trades, {oos_per:>9.5f}% /trade "
          f"({oos_per / COST_PCT:>5.2f}x cost), {total_pct:>9.2f}% total")
    print(f"  cells still above threshold out-of-sample: {len(held)} of {len(selected)}"
          f" ({', '.join(f'{c}/{k.upper()}' for c, k in held) or 'none'})")

    edges_is = [edge_pct(ins[c], k) for c, k in selected]
    edges_oos = [edge_pct(oos[c], k) for c, k in selected]
    if len(selected) > 1:
        print(f"  mean cell edge  in-sample {statistics.mean(edges_is):.5f}%"
              f"  ->  out-of-sample {statistics.mean(edges_oos):.5f}%")


if __name__ == "__main__":
    main()
