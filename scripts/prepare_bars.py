"""
Resample FirstRate 1-minute futures data into N-minute bars, carrying the
execution price the backtest should actually fill at.

Bar convention
--------------
Minutes are grouped by quarter-hour and the bar is LABELLED BY ITS LAST MINUTE,
so a 15-minute bar closing at 13:59 covers 13:45..13:59, the next closes at
14:14 covering 14:00..14:14, and so on.

Fill convention
---------------
The signal is computed on the bar's close (13:59), and the fill happens at the
close of the FIRST one-minute bar of the next group (14:00). That is exactly
one minute of reaction time and carries no look-ahead: 14:00's close is not
known when 13:59 prints.

Because the fill price is the first minute of the next group, it falls out of
the grouping for free - no separate lookup, no off-by-one.

Session gaps
------------
Futures sessions break. When the next group does not start on the very next
minute the fill still uses its first bar, and the gap is written to
`fill_gap_min` so it can be inspected or filtered downstream.

Run
---
    .venv\\Scripts\\python.exe scripts\\prepare_bars.py \\
        --input data\\E6_full_1min_continuous_ratio_adjusted.txt \\
        --output data\\E6_15min.csv
"""

import argparse
import csv
import os
from collections import Counter
from datetime import datetime
from datetime import timedelta


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resample 1-minute bars into N-minute bars with a next-minute fill price.",
    )
    parser.add_argument("--input", required=True, help="1-minute CSV (no header)")
    parser.add_argument("--output", required=True, help="Destination CSV")
    parser.add_argument("--minutes", type=int, default=15,
                        help="Bar size in minutes; must divide 60 (default 15)")
    parser.add_argument("--start", help="Keep bars from this date (YYYY-MM-DD)")
    parser.add_argument("--end", help="Keep bars up to this date (YYYY-MM-DD)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if 60 % args.minutes:
        raise SystemExit(f"--minutes must divide 60, got {args.minutes}")

    groups: list[dict] = []
    current: dict | None = None
    current_key: tuple | None = None
    malformed = 0
    rows_read = 0

    with open(args.input, newline="", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(",")
            if len(parts) < 5:
                malformed += 1
                continue
            rows_read += 1

            stamp = parts[0]
            # Fixed-width 'YYYY-MM-DD HH:MM:SS'; slicing beats strptime by a lot
            # over several million rows, and the format is guaranteed here.
            day, hour, minute = stamp[:10], int(stamp[11:13]), int(stamp[14:16])
            if args.start and day < args.start:
                continue
            if args.end and day > args.end:
                continue

            key = (day, hour, minute // args.minutes)
            if key != current_key:
                if current is not None:
                    groups.append(current)
                current_key = key
                current = {
                    "first_stamp": stamp,
                    "last_stamp": stamp,
                    "open": parts[1],
                    "high": float(parts[2]),
                    "low": float(parts[3]),
                    "close": parts[4],
                    "first_close": parts[4],
                    "minutes": 1,
                }
            else:
                current["last_stamp"] = stamp
                current["high"] = max(current["high"], float(parts[2]))
                current["low"] = min(current["low"], float(parts[3]))
                current["close"] = parts[4]
                current["minutes"] += 1

    if current is not None:
        groups.append(current)

    print(f"Read {rows_read} one-minute rows"
          f"{f' ({malformed} malformed skipped)' if malformed else ''}")
    print(f"Grouped into {len(groups)} {args.minutes}-minute bars")

    # The fill for bar i is the FIRST minute of bar i+1: its close is the price
    # one minute after the signal. The last bar has no successor, so it is
    # dropped rather than filled with a price that does not exist.
    fmt = "%Y-%m-%d %H:%M:%S"
    gap_counter: Counter = Counter()
    written = 0

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", newline="", encoding="utf-8") as out:
        writer = csv.writer(out)
        writer.writerow([
            "timestamp", "open", "high", "low", "close",
            "fill_timestamp", "fill_price", "fill_gap_min", "minutes_in_bar",
        ])
        for i in range(len(groups) - 1):
            bar, nxt = groups[i], groups[i + 1]
            bar_end = datetime.strptime(bar["last_stamp"], fmt)
            fill_at = datetime.strptime(nxt["first_stamp"], fmt)
            gap = int((fill_at - bar_end) / timedelta(minutes=1))
            gap_counter[gap] += 1
            writer.writerow([
                bar["last_stamp"], bar["open"], f"{bar['high']:.5f}",
                f"{bar['low']:.5f}", bar["close"],
                nxt["first_stamp"], nxt["first_close"], gap, bar["minutes"],
            ])
            written += 1

    print(f"Wrote {written} bars to {args.output}")
    print()
    print("Fill gap (minutes between bar close and fill bar):")
    for gap, count in sorted(gap_counter.items())[:6]:
        print(f"  {gap:>6} min : {count:>8} bars ({100.0 * count / written:5.2f}%)")
    big = sum(c for g, c in gap_counter.items() if g > 5)
    if big:
        print(f"  > 5   min : {big:>8} bars ({100.0 * big / written:5.2f}%) "
              f"- session breaks; filter on fill_gap_min if they matter")

    short_bars = sum(1 for g in groups if g["minutes"] < args.minutes)
    print()
    print(f"Bars with fewer than {args.minutes} one-minute rows: {short_bars} "
          f"({100.0 * short_bars / len(groups):.2f}%) - thin session edges, kept")


if __name__ == "__main__":
    main()
