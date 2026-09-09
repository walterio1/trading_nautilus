"""
Variance-ratio and efficiency-ratio regimes, measured against the 8-state scheme.

The existing regime layer classifies a segment by the SIGN of three strategies'
PnL over the previous segment. That is a noisy proxy: a ~26-bar segment carries
only about two trades per pairing, so its PnL is dominated by the particular
path rather than by any property of the market. The statistics here read the
same segment but use every bar in it.

  VR(2)  variance of 2-bar returns over twice the variance of 1-bar returns.
         Below 1 means mean reversion, above 1 means trending. VR(2) is
         essentially 1 + rho(1), so it is estimable from a short segment,
         unlike VR at longer lags which needs far more bars than a segment has.
  ER     |net move| / sum |bar moves|, Kaufman's efficiency ratio. Near 1 the
         segment is a straight line, near 0 it is chop. Better behaved than VR
         in small samples because it needs no variance estimate.

Both are continuous, so segments are ranked into quantiles instead of being
forced into eight sparse buckets, and neither adds a parameter: the window is
the segment, which the slow-superSlow crossover already defines.

Buckets are assigned on the full sample, then the SAME buckets are scored on
each chronological half separately. A discriminator worth using ranks them the
same way in both halves; one that reorders is fitted to the sample.

Everything is reversion edge per TRANSACTION in percent of notional, against
the ~0.00455% cost of one tick.
"""

import argparse
import csv
import statistics

COST_PCT = 0.00455
PAIRINGS = ("a", "b", "c")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Variance-ratio regimes vs the 8-state scheme.")
    p.add_argument("--pnl", required=True, help="Per-bar series from pnl_series.py")
    p.add_argument("--buckets", type=int, default=5, help="Quantile buckets (default 5)")
    p.add_argument("--min-bars", type=int, default=8,
                   help="Segments shorter than this cannot support a statistic")
    return p.parse_args()


def variance_ratio(closes: list[float]) -> float | None:
    """
    VR(2) from a segment's closes. Returns None when the segment is too short
    or degenerate, rather than a misleading number from three or four points.
    """
    if len(closes) < 5:
        return None
    r = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
    r2 = [r[i] + r[i + 1] for i in range(len(r) - 1)]
    v1 = statistics.pvariance(r)
    if v1 == 0:
        return None
    return statistics.pvariance(r2) / (2 * v1)


def efficiency_ratio(closes: list[float]) -> float | None:
    """|net move| / sum of absolute bar moves."""
    if len(closes) < 3:
        return None
    total = sum(abs(closes[i] - closes[i - 1]) for i in range(1, len(closes)))
    if total == 0:
        return None
    return abs(closes[-1] - closes[0]) / total


def load(path: str) -> list[dict]:
    """One record per segment: its closes, PnL, trade counts and first stamp."""
    segments: dict[int, dict] = {}
    previous = {k: 0 for k in PAIRINGS}
    for row in csv.DictReader(open(path, newline="", encoding="utf-8")):
        sid = int(row["seg_id"])
        seg = segments.setdefault(sid, {
            "closes": [], "stamp": row["timestamp"], "sid": sid,
            **{f"pct_{k}": 0.0 for k in PAIRINGS},
            **{f"ops_{k}": 0 for k in PAIRINGS},
        })
        seg["closes"].append(float(row["close"]))
        for k in PAIRINGS:
            pos = int(row[f"pos_{k}"] or 0)
            seg[f"pct_{k}"] += float(row[f"ret_{k}"])
            if pos != previous[k] and pos != 0:
                seg[f"ops_{k}"] += 1
            previous[k] = pos
    ids = sorted(segments)[1:-1]
    return [segments[i] for i in ids]


def edge(chunk: list[dict], k: str) -> float:
    """Reversion edge per transaction, in percent."""
    pct = sum(-c[f"pct_{k}"] for c in chunk)
    ops = sum(c[f"ops_{k}"] for c in chunk)
    return pct / ops if ops else 0.0


def report(name: str, scored: list[tuple], buckets: int) -> None:
    """`scored` is (statistic of previous segment, current segment) pairs."""
    scored = sorted(scored, key=lambda x: x[0])
    n = len(scored)
    # Bucket index per pair, assigned on the full sample.
    tagged = []
    for b in range(buckets):
        lo, hi = b * n // buckets, (b + 1) * n // buckets
        for stat, cur in scored[lo:hi]:
            tagged.append((b, stat, cur))

    print(f"\n{name}  ({n} segments)")
    header = (f"{'bucket':<8}{'stat range':>18}{'segs':>7}"
              + "".join(f"{p.upper() + ' all':>10}{'x':>6}{p.upper() + ' 1st':>10}"
                        f"{p.upper() + ' 2nd':>10}" for p in PAIRINGS))
    print(header)
    print("-" * len(header))

    chrono = sorted(tagged, key=lambda x: x[2]["sid"])
    half = len(chrono) // 2
    first_ids = {id(x[2]) for x in chrono[:half]}

    for b in range(buckets):
        rows = [t for t in tagged if t[0] == b]
        stats_in_bucket = [t[1] for t in rows]
        cur_all = [t[2] for t in rows]
        cur_1st = [t[2] for t in rows if id(t[2]) in first_ids]
        cur_2nd = [t[2] for t in rows if id(t[2]) not in first_ids]
        cells = ""
        for k in PAIRINGS:
            e = edge(cur_all, k)
            cells += (f"{e:>10.5f}{e / COST_PCT:>6.2f}"
                      f"{edge(cur_1st, k):>10.5f}{edge(cur_2nd, k):>10.5f}")
        rng = f"{min(stats_in_bucket):+.3f}..{max(stats_in_bucket):+.3f}"
        print(f"{'Q' + str(b + 1):<8}{rng:>18}{len(rows):>7}{cells}")


def main() -> None:
    args = parse_args()
    segments = load(args.pnl)
    print(f"{len(segments)} segments from {args.pnl}")

    vr_pairs, er_pairs = [], []
    for j in range(1, len(segments)):
        prev, cur = segments[j - 1], segments[j]
        if len(prev["closes"]) < args.min_bars:
            continue
        vr = variance_ratio(prev["closes"])
        er = efficiency_ratio(prev["closes"])
        if vr is not None:
            vr_pairs.append((vr, cur))
        if er is not None:
            er_pairs.append((er, cur))

    vrs = [v for v, _ in vr_pairs]
    print(f"  VR(2) of previous segment: median {statistics.median(vrs):.3f}, "
          f"mean {statistics.mean(vrs):.3f}, "
          f"{100 * sum(1 for v in vrs if v < 1) / len(vrs):.1f}% below 1 (reverting)")

    report("VARIANCE RATIO VR(2) of the previous segment", vr_pairs, args.buckets)
    report("EFFICIENCY RATIO of the previous segment", er_pairs, args.buckets)
    print(f"\nOne tick costs {COST_PCT:.5f}% per transaction. "
          f"'1st'/'2nd' are the same buckets scored on each chronological half.")

    # Decisive test for the efficiency ratio: pick the cut on the first half
    # only, then apply it untouched to the second. `er_pairs` is already in
    # chronological order because segments are appended in order.
    half = len(er_pairs) // 2
    first, second = er_pairs[:half], er_pairs[half:]
    print("\nOUT-OF-SAMPLE - ER cut chosen on the first half, applied to the second")
    header = f"{'ER cut':>8}" + "".join(
        f"{p.upper() + ' 1st':>9}{'ops':>7}{p.upper() + ' 2nd':>9}{'ops':>7}" for p in PAIRINGS)
    print(header)
    print("-" * len(header))
    for cut in (0.028, 0.040, 0.055, 0.083, 0.112, 0.147):
        line = f"{cut:>8.3f}"
        for k in PAIRINGS:
            a = [c for e, c in first if e < cut]
            b = [c for e, c in second if e < cut]
            line += (f"{edge(a, k) / COST_PCT:>9.2f}{sum(c[f'ops_{k}'] for c in a):>7}"
                     f"{edge(b, k) / COST_PCT:>9.2f}{sum(c[f'ops_{k}'] for c in b):>7}")
        print(line)
    print("  x cost. A cut that only works on the half it was picked from is fitted.")


if __name__ == "__main__":
    main()
