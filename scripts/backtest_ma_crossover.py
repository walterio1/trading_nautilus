"""
Offline backtest runner for the endogenous-window MA crossover.

Replays a CSV of historical bars through the SAME moving-average classes the
live strategy uses (imported from ibkr_paper_ma_crossover, never re-implemented
here) and reports every crossover pairing side by side, so one pass over the
data answers "fast vs slow vs super_slow".

No TWS/IB Gateway and no TradingNode: this only needs the indicator classes and
a price series, so it runs on any timeframe you can get data for (minute, hour,
day) rather than the 20-second live bars.

Run
---
    # smoke test with a synthetic random walk (no data file needed)
    .venv\\Scripts\\python.exe scripts\\backtest_ma_crossover.py --synthetic 5000

    # real data
    .venv\\Scripts\\python.exe scripts\\backtest_ma_crossover.py --csv data\\eurusd_1h.csv

    # sweep the super_slow depth and both slow constructions
    .venv\\Scripts\\python.exe scripts\\backtest_ma_crossover.py --csv data\\eurusd_1h.csv \\
        --levels 2,3,4 --slow-modes smoothed_runs,lagged_fast --audit-csv out.csv

Execution model
---------------
Faithful to the live strategy where it matters, and deliberately pessimistic
where it does not:

  * Same rule: hold long while the pair's faster leg is above its slower leg,
    short while below. State-based, not event-based - exactly as on_bar does.
  * A reversal is ONE order of |position| + trade_size, so it is charged one
    spread crossing, matching _reverse_to.
  * Fills happen at the NEXT bar (open if the CSV has one, else close). The
    signal is computed on a bar's close, so filling on that same close would
    be look-ahead; this avoids it.
  * The position is flattened on the last bar, mirroring on_stop.

Warm-up alignment
-----------------
By default every pairing starts trading on the same bar - the first one where
ALL THREE MAs are initialized - so the comparison is like-for-like. Without
that, fast_vs_slow would get a head start of tens of bars over any pairing
involving super_slow and the PnLs would not be comparable. Pass --no-align to
let each pairing start as soon as its own two legs are ready (which is what
the live strategy does).
"""

import argparse
import csv
import os
import random
import sys
from dataclasses import dataclass
from dataclasses import field
from decimal import Decimal
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ibkr_paper_ma_crossover import CROSSOVER_PAIRS  # noqa: E402
from ibkr_paper_ma_crossover import SLOW_MA_BUILDERS  # noqa: E402
from ibkr_paper_ma_crossover import LaggedWindowMovingAverage  # noqa: E402
from ibkr_paper_ma_crossover import RunLengthMovingAverage  # noqa: E402


# Candidate column names, tried case-insensitively against the CSV header.
TIMESTAMP_COLUMNS = ("timestamp", "datetime", "date", "time", "ts", "date_time")
CLOSE_COLUMNS = ("close", "close_price", "last", "price", "adj_close", "c")
OPEN_COLUMNS = ("open", "open_price", "o")


@dataclass
class Snapshot:
    """One MA's state on one bar."""

    initialized: bool
    value: float
    period: int
    state: str


@dataclass
class Trade:
    entry_index: int
    exit_index: int
    side: int          # +1 long, -1 short
    quantity: Decimal
    entry_price: Decimal
    exit_price: Decimal
    pnl: Decimal       # Gross, in quote currency, before costs


@dataclass
class Result:
    pair: str
    slow_mode: str
    levels: int
    first_signal_bar: int | None = None
    trades: list[Trade] = field(default_factory=list)
    gross_pnl: Decimal = Decimal(0)
    costs: Decimal = Decimal(0)
    max_drawdown: Decimal = Decimal(0)
    bars_traded: int = 0
    flat_bars: int = 0        # Bars where the two legs are exactly equal
    equal_window_bars: int = 0  # ...of which, because N_faster == N_slower

    @property
    def flat_pct(self) -> float:
        return 100.0 * self.flat_bars / self.bars_traded if self.bars_traded else 0.0

    @property
    def net_pnl(self) -> Decimal:
        return self.gross_pnl - self.costs

    @property
    def wins(self) -> int:
        return sum(1 for t in self.trades if t.pnl > 0)

    @property
    def win_rate(self) -> float:
        return 100.0 * self.wins / len(self.trades) if self.trades else 0.0


def _find_column(header: list[str], candidates: tuple[str, ...]) -> str | None:
    lowered = {name.strip().lower(): name for name in header}
    for candidate in candidates:
        if candidate in lowered:
            return lowered[candidate]
    return None


def load_bars(path: str, timestamp_col: str | None, close_col: str | None,
              open_col: str | None, fill_col: str | None = None,
              max_fill_gap: int | None = None,
              ) -> tuple[list[str], list[Decimal], list[Decimal] | None, list[Decimal] | None]:
    """
    Read bars from `path`. Returns (timestamps, closes, opens, explicit fills).

    Column names are auto-detected unless given explicitly. Only a close is
    required; an open, when present, is used as the fill price so that no
    signal is filled on the bar that produced it.

    `fill_col` names a column holding the price to execute the bar's signal at
    - written by prepare_bars.py as the close of the first minute AFTER the
    bar. When given it overrides the open/next-close fallback, because it
    already encodes the intended execution timing.

    `max_fill_gap` drops bars whose fill sits further than that many minutes
    after the close (session breaks), read from a `fill_gap_min` column.
    """
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise SystemExit(f"{path}: empty or headerless CSV")
        header = list(reader.fieldnames)

        ts_name = timestamp_col or _find_column(header, TIMESTAMP_COLUMNS)
        close_name = close_col or _find_column(header, CLOSE_COLUMNS)
        open_name = open_col or _find_column(header, OPEN_COLUMNS)
        if close_name is None:
            raise SystemExit(
                f"{path}: could not find a close column among {header}. "
                f"Pass --close-col explicitly.",
            )

        if fill_col is not None and fill_col not in header:
            raise SystemExit(f"{path}: no column {fill_col!r} among {header}")

        timestamps: list[str] = []
        closes: list[Decimal] = []
        opens: list[Decimal] = []
        fills: list[Decimal] = []
        dropped_gap = 0
        for lineno, row in enumerate(reader, start=2):
            raw_close = (row.get(close_name) or "").strip()
            if not raw_close:
                continue  # Blank row (common at the tail of exported files)
            if max_fill_gap is not None and row.get("fill_gap_min"):
                if int(row["fill_gap_min"]) > max_fill_gap:
                    dropped_gap += 1
                    continue
            try:
                close = Decimal(raw_close)
            except Exception:
                raise SystemExit(f"{path}:{lineno}: cannot parse close {raw_close!r}")
            closes.append(close)
            timestamps.append((row.get(ts_name) or str(lineno)).strip() if ts_name else str(lineno))
            if open_name is not None:
                raw_open = (row.get(open_name) or "").strip()
                opens.append(Decimal(raw_open) if raw_open else close)
            if fill_col is not None:
                raw_fill = (row.get(fill_col) or "").strip()
                fills.append(Decimal(raw_fill) if raw_fill else close)

    if len(closes) < 2:
        raise SystemExit(f"{path}: need at least 2 usable rows, got {len(closes)}")

    if fill_col is not None:
        fill_desc = f"explicit column {fill_col!r} (no look-ahead)"
    elif open_name:
        fill_desc = f"next bar open ({open_name})"
    else:
        fill_desc = "next bar close"

    print(f"Loaded {len(closes)} bars from {path}")
    print(f"  timestamp column: {ts_name or '(none, using row numbers)'}")
    print(f"  close column    : {close_name}")
    print(f"  fill price      : {fill_desc}")
    if dropped_gap:
        print(f"  dropped         : {dropped_gap} bars with fill_gap_min > {max_fill_gap}")
    print(f"  range           : {timestamps[0]} .. {timestamps[-1]}")
    return (timestamps, closes,
            (opens if open_name is not None else None),
            (fills if fill_col is not None else None))


def synthetic_bars(count: int, seed: int) -> tuple[list[str], list[Decimal], None]:
    """A random walk on a 0.5-pip grid, for smoke-testing the runner."""
    rng = random.Random(seed)
    price = Decimal("1.08500")
    closes = []
    for _ in range(count):
        price += Decimal(rng.choice(["0.00005", "-0.00005", "0.00000"]))
        closes.append(price)
    print(f"Generated {count} synthetic bars (seed={seed}) - smoke test only, not real data")
    return [str(i) for i in range(1, count + 1)], closes, None


def compute_series(closes: list[Decimal], slow_modes: list[str],
                   levels_list: list[int]) -> dict[str, list[Snapshot]]:
    """
    Run every MA over the price series in a single pass.

    The fast MA is computed once and shared: both slower constructions consume
    its endogenous window, exactly as on_bar does, so a given slow_mode or
    level yields the same series it would live.
    """
    fast = RunLengthMovingAverage()
    slows = {
        mode: SLOW_MA_BUILDERS[mode](SimpleNamespace(super_slow_levels=levels_list[0]))
        for mode in slow_modes
    }
    supers = {level: LaggedWindowMovingAverage(levels=level) for level in levels_list}

    series: dict[str, list[Snapshot]] = {"fast_ma": []}
    for mode in slow_modes:
        series[f"slow_ma:{mode}"] = []
    for level in levels_list:
        series[f"super_slow_ma:{level}"] = []

    def snap(ma) -> Snapshot:
        return Snapshot(ma.initialized, ma.value, ma.period, ma.state_repr())

    for close in closes:
        fast.update_raw(close)
        series["fast_ma"].append(snap(fast))
        for mode, ma in slows.items():
            ma.update_raw(close, fast.period)
            series[f"slow_ma:{mode}"].append(snap(ma))
        for level, ma in supers.items():
            ma.update_raw(close, fast.period)
            series[f"super_slow_ma:{level}"].append(snap(ma))
    return series


def _series_key(name: str, slow_mode: str, level: int) -> str:
    if name == "slow_ma":
        return f"slow_ma:{slow_mode}"
    if name == "super_slow_ma":
        return f"super_slow_ma:{level}"
    return name


def simulate(pair: str, slow_mode: str, level: int, closes: list[Decimal],
             opens: list[Decimal] | None, series: dict[str, list[Snapshot]],
             trade_size: Decimal, half_spread: Decimal,
             start_bar: int, invert: bool = False,
             fill_at_close: bool = False,
             fills: list[Decimal] | None = None) -> tuple[Result, list[dict]]:
    """
    Replay one pairing. Returns the result plus per-bar audit rows.

    Positions are signed base-currency units; PnL is in quote currency:
    a position of Q closed from `entry` to `exit` earns Q * (exit - entry),
    which is negative for a short whose price rose, as it should be.

    `invert` trades the crossover backwards (mean reversion instead of trend
    following): long when the faster leg is BELOW the slower one. Note what
    this does and does not change - it negates gross PnL exactly, but leaves
    the trade count, and therefore the cost, untouched. Inverting a strategy
    whose losses are transaction costs just produces another losing strategy.
    """
    faster_name, slower_name = CROSSOVER_PAIRS[pair]
    faster = series[_series_key(faster_name, slow_mode, level)]
    slower = series[_series_key(slower_name, slow_mode, level)]

    result = Result(pair=pair, slow_mode=slow_mode, levels=level)
    rows: list[dict] = []

    position = Decimal(0)
    entry_price = Decimal(0)
    entry_index = 0
    realized = Decimal(0)
    peak_equity = Decimal(0)
    prev_diff: float | None = None

    n = len(closes)
    for i in range(n):
        f, s = faster[i], slower[i]
        row = {
            "index": i,
            "close": closes[i],
            "faster": f.value if f.initialized else "",
            "n_faster": f.period if f.initialized else "",
            "slower": s.value if s.initialized else "",
            "n_slower": s.period if s.initialized else "",
            "diff": "",
            "trend": "",
            "trade": "",
            "position": position,
            "realized_pnl": realized,
        }

        tradeable = f.initialized and s.initialized and i >= start_bar
        if tradeable:
            result.bars_traded += 1
            diff = f.value - s.value
            trend = "no_cross"
            if prev_diff is not None:
                if prev_diff <= 0 < diff:
                    trend = "CROSSED_UP"
                elif prev_diff >= 0 > diff:
                    trend = "CROSSED_DOWN"
            prev_diff = diff
            row["diff"] = f"{diff:.6f}"
            row["trend"] = trend

            # A dead bar: both legs average the same prices, so the crossover
            # has no sign and the strategy cannot act. Structural whenever the
            # two windows coincide, since both MAs are the mean of the last N
            # prices ending at t - identical N means an identical number.
            if diff == 0:
                result.flat_bars += 1
                if f.period == s.period:
                    result.equal_window_bars += 1

            # Fill on the NEXT bar, so a close-derived signal never fills on
            # its own close. The final bar has no successor to fill on.
            if fills is not None or fill_at_close or i + 1 < n:
                # The traded signal; the audit row keeps the raw diff so the
                # inverted run stays comparable to the straight one bar by bar.
                signal = -diff if invert else diff
                want_long = signal > 0
                want_short = signal < 0
                side = 0
                if want_long and position <= 0:
                    side = 1
                elif want_short and position >= 0:
                    side = -1

                if side != 0:
                    if fills is not None:
                        fill = fills[i]
                    elif fill_at_close:
                        fill = closes[i]
                    else:
                        fill = opens[i + 1] if opens is not None else closes[i + 1]
                    quantity = abs(position) + trade_size
                    if position != 0:
                        pnl = position * (fill - entry_price)
                        realized += pnl
                        result.gross_pnl += pnl
                        result.trades.append(Trade(
                            entry_index=entry_index, exit_index=i + 1,
                            side=1 if position > 0 else -1, quantity=abs(position),
                            entry_price=entry_price, exit_price=fill, pnl=pnl,
                        ))
                    cost = quantity * half_spread
                    result.costs += cost
                    realized -= cost
                    position = trade_size if side > 0 else -trade_size
                    entry_price = fill
                    entry_index = i + 1
                    if result.first_signal_bar is None:
                        result.first_signal_bar = i + 1
                    row["trade"] = f"{'+' if side > 0 else '-'}{quantity}"

        # Mark to market for the drawdown curve.
        equity = realized + (position * (closes[i] - entry_price) if position != 0 else Decimal(0))
        peak_equity = max(peak_equity, equity)
        result.max_drawdown = max(result.max_drawdown, peak_equity - equity)
        row["position"] = position
        row["realized_pnl"] = realized
        rows.append(row)

    # Flatten on the last bar, mirroring on_stop's _flatten_position.
    if position != 0:
        fill = closes[-1]
        pnl = position * (fill - entry_price)
        realized += pnl
        result.gross_pnl += pnl
        result.costs += abs(position) * half_spread
        result.trades.append(Trade(
            entry_index=entry_index, exit_index=n - 1,
            side=1 if position > 0 else -1, quantity=abs(position),
            entry_price=entry_price, exit_price=fill, pnl=pnl,
        ))

    return result, rows


def first_aligned_bar(series: dict[str, list[Snapshot]]) -> int:
    """First bar on which every computed MA is initialized."""
    n = len(next(iter(series.values())))
    for i in range(n):
        if all(snapshots[i].initialized for snapshots in series.values()):
            return i
    return n


def parse_list(value: str, cast):
    return [cast(part.strip()) for part in value.split(",") if part.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backtest the endogenous-window MA crossover over historical bars.",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--csv", help="Path to a CSV of historical bars")
    source.add_argument("--synthetic", type=int, metavar="N",
                        help="Generate N synthetic bars instead (smoke test)")
    parser.add_argument("--seed", type=int, default=7, help="Seed for --synthetic")
    parser.add_argument("--timestamp-col", help="Override timestamp column name")
    parser.add_argument("--close-col", help="Override close column name")
    parser.add_argument("--open-col", help="Override open column name")
    parser.add_argument("--fill-col", help="Column holding the execution price for "
                                           "each bar's signal (see prepare_bars.py)")
    parser.add_argument("--max-fill-gap", type=int,
                        help="Drop bars whose fill_gap_min exceeds this (session breaks)")
    parser.add_argument("--pairs", default=",".join(CROSSOVER_PAIRS),
                        help="Comma-separated crossover pairs to evaluate")
    parser.add_argument("--slow-modes", default=",".join(sorted(SLOW_MA_BUILDERS)),
                        help="Comma-separated slow-window constructions")
    parser.add_argument("--levels", default="3",
                        help="Comma-separated super_slow depths to sweep")
    parser.add_argument("--trade-size", type=Decimal, default=Decimal(20000),
                        help="Position size in base-currency units")
    parser.add_argument("--spread-pips", type=Decimal, default=Decimal("0.5"),
                        help="Full bid/ask spread in pips; each fill crosses half")
    parser.add_argument("--pip-size", type=Decimal, default=Decimal("0.0001"),
                        help="Price increment of one pip")
    parser.add_argument("--no-align", action="store_true",
                        help="Let each pairing start as soon as its own legs are ready")
    parser.add_argument("--invert", action="store_true",
                        help="Trade the crossover backwards (mean reversion)")
    parser.add_argument("--both-directions", action="store_true",
                        help="Report each pairing straight AND inverted, side by side")
    parser.add_argument("--fill-at-close", action="store_true",
                        help="Fill on the signal bar's own close. LOOK-AHEAD: that "
                             "price is gone once the bar prints. Use to isolate the "
                             "raw signal, never to size a live expectation.")
    parser.add_argument("--audit-csv", help="Write per-bar rows for the best pairing here")
    args = parser.parse_args()

    pairs = parse_list(args.pairs, str)
    slow_modes = parse_list(args.slow_modes, str)
    levels_list = parse_list(args.levels, int)
    for pair in pairs:
        if pair not in CROSSOVER_PAIRS:
            raise SystemExit(f"Unknown pair {pair!r}, expected one of {sorted(CROSSOVER_PAIRS)}")
    for mode in slow_modes:
        if mode not in SLOW_MA_BUILDERS:
            raise SystemExit(f"Unknown slow mode {mode!r}, expected one of {sorted(SLOW_MA_BUILDERS)}")

    if args.synthetic:
        timestamps, closes, opens = synthetic_bars(args.synthetic, args.seed)
        fills = None
    else:
        timestamps, closes, opens, fills = load_bars(
            args.csv, args.timestamp_col, args.close_col, args.open_col,
            args.fill_col, args.max_fill_gap,
        )

    half_spread = args.spread_pips * args.pip_size / 2
    print(f"  trade size      : {args.trade_size}")
    print(f"  spread          : {args.spread_pips} pips (half-spread {half_spread} per fill)")
    if args.fill_at_close:
        print("  fill timing     : SIGNAL BAR CLOSE - look-ahead, gross signal only")
    if half_spread == 0:
        print("  costs           : ZERO - frictionless, gross PnL only")

    directions = [False, True] if args.both_directions else [args.invert]
    results: list[tuple[Result, list[dict]]] = []
    for level in levels_list:
        series = compute_series(closes, slow_modes, [level])
        align_bar = 0 if args.no_align else first_aligned_bar(series)
        if not args.no_align and level == levels_list[0]:
            print(f"  warm-up aligned : all MAs ready at bar {align_bar} "
                  f"(of {len(closes)}); trading starts there for every pairing")
        for mode in slow_modes:
            for pair in pairs:
                for invert in directions:
                    result, rows = simulate(
                        pair, mode, level, closes, opens, series,
                        args.trade_size, half_spread, align_bar, invert,
                        args.fill_at_close, fills,
                    )
                    result.pair = f"{pair}{' [inv]' if invert else ''}"
                    results.append((result, rows))

    print()
    header = (f"{'pair':<26}{'slow mode':<16}{'L':>3}{'trades':>8}{'win%':>7}"
              f"{'gross':>12}{'costs':>10}{'net':>12}{'maxDD':>10}{'flat%':>7}{'1st bar':>9}")
    print(header)
    print("-" * len(header))
    for result, _ in results:
        print(f"{result.pair:<26}{result.slow_mode:<16}{result.levels:>3}"
              f"{len(result.trades):>8}{result.win_rate:>7.1f}"
              f"{float(result.gross_pnl):>12.2f}{float(result.costs):>10.2f}"
              f"{float(result.net_pnl):>12.2f}{float(result.max_drawdown):>10.2f}"
              f"{result.flat_pct:>7.1f}"
              f"{result.first_signal_bar if result.first_signal_bar is not None else '-':>9}")

    dead = [r for r, _ in results if r.equal_window_bars]
    if dead:
        print()
        print("Dead bars (both legs numerically identical because their windows "
              "coincide):")
        for result in dead:
            print(f"  {result.pair}/{result.slow_mode}/L={result.levels}: "
                  f"{result.equal_window_bars} bars "
                  f"({100.0 * result.equal_window_bars / result.bars_traded:.1f}%) "
                  f"with N_faster == N_slower")
        print("  Only possible when the two windows can be equal; any pairing "
              "built on the lagged rule has N_slower >= N_faster + 2 by "
              "construction and cannot hit this.")

    tradeable = [r for r, _ in results if r.trades]
    if tradeable:
        best = max(tradeable, key=lambda r: r.net_pnl)
        print()
        print(f"Best net PnL: {best.pair} / {best.slow_mode} / L={best.levels} "
              f"-> {float(best.net_pnl):.2f} over {len(best.trades)} trades")
        print("Note: costs are spread only, no commission; a single run on one "
              "series is not evidence of edge.")

    if args.audit_csv:
        best_rows = max(results, key=lambda pair_rows: pair_rows[0].net_pnl)
        result, rows = best_rows
        fieldnames = ["timestamp", "index", "close", "faster", "n_faster",
                      "slower", "n_slower", "diff", "trend", "trade",
                      "position", "realized_pnl"]
        with open(args.audit_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                row = dict(row)
                row["timestamp"] = timestamps[row["index"]]
                writer.writerow(row)
        print(f"Wrote {len(rows)} rows for {result.pair}/{result.slow_mode}/"
              f"L={result.levels} to {args.audit_csv}")


if __name__ == "__main__":
    main()
