# aún no he hecho el commit push 
# drawio? 
# probar barras 15 min con las mm endógenas actuales
# ver si hallo mecanismo de endogeneización para N mucho mayor

"""
Minimal paper-trading test: SMA crossover strategy on Interactive Brokers (TWS/IB Gateway).

Prerequisites
-------------
1. TWS or IB Gateway running and logged into a PAPER TRADING account.
2. API connections enabled in TWS/Gateway:
     Configuration -> API -> Settings -> "Enable ActiveX and Socket Clients"
   and "Read-Only API" UNCHECKED if you want this script to submit orders.
3. Note the socket port shown there (defaults below assume IB Gateway paper: 4002).

Run
---
    .venv\\Scripts\\python.exe scripts\\ibkr_paper_ma_crossover.py

Stop with Ctrl+C (the node will disconnect and shut down cleanly).
"""

import csv
import os
from collections import deque
from datetime import timedelta
from decimal import ROUND_HALF_UP
from decimal import Decimal

from nautilus_trader.adapters.interactive_brokers.common import IB
from nautilus_trader.adapters.interactive_brokers.common import IB_VENUE
from nautilus_trader.adapters.interactive_brokers.common import IBContract
from nautilus_trader.adapters.interactive_brokers.config import (
    InteractiveBrokersDataClientConfig,
)
from nautilus_trader.adapters.interactive_brokers.config import (
    InteractiveBrokersExecClientConfig,
)
from nautilus_trader.adapters.interactive_brokers.config import (
    InteractiveBrokersInstrumentProviderConfig,
)
from nautilus_trader.adapters.interactive_brokers.factories import (
    InteractiveBrokersLiveDataClientFactory,
)
from nautilus_trader.adapters.interactive_brokers.factories import (
    InteractiveBrokersLiveExecClientFactory,
)
from nautilus_trader.common.component import TimeEvent
from nautilus_trader.config import LoggingConfig
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.core.datetime import unix_nanos_to_dt
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.events import OrderCanceled
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.model.events import OrderRejected
from nautilus_trader.model.events import PositionChanged
from nautilus_trader.model.events import PositionClosed
from nautilus_trader.model.events import PositionOpened
from nautilus_trader.model.identifiers import ClientOrderId
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy
from nautilus_trader.trading.strategy import StrategyConfig


# ============================================================================
# CONNECTION PARAMETERS (placeholders — edit these for your setup)
# ============================================================================
IBG_HOST = "127.0.0.1"          # Host running TWS / IB Gateway
IBG_PORT = 7497                 # 4002 = IB Gateway paper, 4001 = IB Gateway live
                                 # 7497 = TWS paper,        7496 = TWS live
IBG_CLIENT_ID = 1               # Unique per connected API client
IB_ACCOUNT_ID = "DUO087437"     # Your IB paper account ID (e.g. "DU1234567")

# ============================================================================
# STRATEGY / INSTRUMENT PARAMETERS (placeholders — edit these)
# ============================================================================
# FX spot pair: IB streams real-time IDEALPRO FX quotes for free, no market
# data subscription required (unlike US stocks, which need a paid Level 1
# subscription for real-time quotes — see the DELAYED_FROZEN alternative
# in InteractiveBrokersDataClientConfig if you want to stay on stocks).
SEC_TYPE = "CASH"                # "CASH" = FX spot
BASE_CURRENCY = "EUR"            # Base currency of the pair
QUOTE_CURRENCY = "USD"           # Quote/settlement currency of the pair
EXCHANGE = "IDEALPRO"            # IB's FX ECN

# Simplified IB symbology instrument id for FX: "<base>/<quote>.<exchange>"
INSTRUMENT_ID = InstrumentId.from_str(f"{BASE_CURRENCY}/{QUOTE_CURRENCY}.{EXCHANGE}")

BAR_SPEC = "20-SECOND-MID"       # BarSpecification: step-aggregation-price_type
                                 # e.g. "1-MINUTE-MID", "5-SECOND-MID", "20-SECOND-MID"
BAR_AGGREGATION_SOURCE = "INTERNAL"  # "EXTERNAL" = IB's native real-time bars
                                     #   (IB only streams these at a fixed 5-SECOND step)
                                     # "INTERNAL" = Nautilus builds bars locally from
                                     #   quote ticks — required for any other step
                                     #   (e.g. "1-SECOND", "10-SECOND", "20-SECOND")
# Neither MA has a period parameter: both windows are derived from the
# series' own run dynamics. The fast MA reads the runs of the raw increments
# (RunLengthMovingAverage); the slow MA reads the runs of the moving average
# of those increments (SmoothedRunLengthMovingAverage).
TRADE_SIZE = Decimal(20000)      # Target position size (FX base currency units;
                                 # IDEALPRO's typical minimum is 20,000). This is
                                 # the size the strategy holds after an entry; a
                                 # reversal sends |current position| + TRADE_SIZE
                                 # in one order (see _reverse_to). No client-side
                                 # cash pre-check - IB rejects the order itself
                                 # if funds are short.
HEARTBEAT_INTERVAL_SECONDS = 30  # How often to log account/position/indicator status

# ============================================================================
# LOGGING (traces are written to stdout AND to a rotating log file)
# ============================================================================
LOG_DIRECTORY = "logs"
LOG_LEVEL_CONSOLE = "INFO"
LOG_LEVEL_FILE = "DEBUG"

# ============================================================================
# AUDIT TRAIL (CSV written on Ctrl-C or normal shutdown)
# ============================================================================
AUDIT_DIRECTORY = "audit"


# Hard cap on the price buffer. N(t) is the sum of two live runs and two
# completed ones, so it only reaches this size under a run structure that
# cannot occur on real data; the cap just bounds memory and slicing cost.
MAX_ENDOGENOUS_WINDOW = 4096


class RunLengthWindow:
    """
    Turns a stream of signs (+1 / -1 / 0) into an endogenous window length
    N(t), read off the run (streak) structure of that stream.

      run_up(t)          consecutive +1 observations up to and including t;
                         reset to 0 as soon as an observation is -1 or 0
      run_down(t)        same for consecutive -1 observations
      prev_run_up(t)     length of the last COMPLETED bullish run (the value
                         run_up held on the observation immediately before
                         its reset); constant until the next bullish run
                         completes
      prev_run_down(t)   same for the bearish side
      N(t)               run_up + run_down + prev_run_up + prev_run_down,
                         defined only once both completed runs exist

    At most one of run_up/run_down is non-zero at any t (both are zero after
    a 0 observation), and every completed run has length >= 1, so N(t) >= 2
    once defined. Each unit of N(t) corresponds to a distinct observation at
    or before t.
    """

    def __init__(self) -> None:
        self.run_up = 0
        self.run_down = 0
        self.prev_run_up: int | None = None
        self.prev_run_down: int | None = None
        self.period = 0  # Current N(t); 0 while still undefined

    @property
    def ready(self) -> bool:
        """Whether one run of each sign has completed, so N(t) is defined."""
        return self.prev_run_up is not None and self.prev_run_down is not None

    def _close_run_up(self) -> None:
        if self.run_up > 0:
            self.prev_run_up = self.run_up
            self.run_up = 0

    def _close_run_down(self) -> None:
        if self.run_down > 0:
            self.prev_run_down = self.run_down
            self.run_down = 0

    def update(self, sign: int) -> int:
        """Feed one sign; returns N(t), or 0 while it is still undefined."""
        if sign > 0:
            self._close_run_down()
            self.run_up += 1
        elif sign < 0:
            self._close_run_up()
            self.run_down += 1
        else:
            # A zero observation closes whichever run was live, starts neither.
            self._close_run_up()
            self._close_run_down()

        if not self.ready:
            return 0

        self.period = self.run_up + self.run_down + self.prev_run_up + self.prev_run_down
        return self.period

    def state_repr(self) -> str:
        return (
            f"run_up={self.run_up} run_down={self.run_down} "
            f"prev_run_up={self.prev_run_up} prev_run_down={self.prev_run_down} "
            f"N={self.period}"
        )


def _sign(value: Decimal) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


class RunLengthMovingAverage:
    """
    Fast MA: window length endogenous, driven by the run dynamics of the
    INCREMENT series (the price series differenced once).

    Fixing a period exogenously adds an arbitrary degree of freedom. Here
    the window adapts to how the series is actually alternating: while a
    streak persists the window stretches, and it contracts back towards the
    length of the last completed streaks when direction flips.

    Base series : increments, d(t) = p(t) - p(t-1)
    Sign        : sign of d(t) - +1 up, -1 down, 0 exactly flat
    Window      : N(t) from RunLengthWindow over those signs
    Value       : mean of the last N(t) PRICES, ending at t

    Prices are carried as Decimal so that "flat" means exactly flat, with no
    float-representation artifacts deciding the sign of a bar.
    """

    def __init__(self) -> None:
        self._prices: deque[Decimal] = deque(maxlen=MAX_ENDOGENOUS_WINDOW)
        self._prev_price: Decimal | None = None

        self.runs = RunLengthWindow()
        self.count = 0        # Observations received
        self.value = 0.0      # Current MM(t)
        self.initialized = False

    @property
    def period(self) -> int:
        return self.runs.period

    def update_raw(self, price: Decimal) -> None:
        self._prices.append(price)
        self.count += 1

        prev_price = self._prev_price
        self._prev_price = price
        if prev_price is None:
            return  # No increment yet

        n = self.runs.update(_sign(price - prev_price))
        if n <= 0 or n > len(self._prices):
            return  # Undefined, or (unreachable) beyond the buffer

        window = list(self._prices)[-n:]
        self.value = float(sum(window) / n)
        self.initialized = True

    def state_repr(self) -> str:
        return self.runs.state_repr()


class SmoothedRunLengthMovingAverage:
    """
    Slow MA: same endogenous-window construction as RunLengthMovingAverage,
    but the run dynamics are read off the MOVING AVERAGE of the increments
    instead of the raw increments.

    Base series : s(t) = mean of the last k increments
    Sign        : sign of s(t)
    Window      : N_slow(t) from RunLengthWindow over those signs
    Value       : mean of the last N_slow(t) PRICES, ending at t

    Two things worth being explicit about:

    1. The mean of the last k increments telescopes,
           s(t) = [p(t) - p(t-k)] / k,
       so sign(s(t)) is exactly the sign of the k-bar momentum. Averaging the
       increments therefore acts as a low-pass filter on the sign stream: it
       only flips when the move over k bars changes direction, not on every
       single bar. Runs get longer, N_slow gets larger, and the MA gets
       slower than the fast one - which is the point.

    2. k is taken from the fast MA's own endogenous window N_fast(t), passed
       in on each update. Using a fixed k would put back exactly the
       arbitrary degree of freedom this construction exists to remove.

    The value is the mean of the last N_slow PRICES (not of the smoothed
    increments) so that both MAs live in price units and their crossover
    remains meaningful.
    """

    def __init__(self) -> None:
        self._prices: deque[Decimal] = deque(maxlen=MAX_ENDOGENOUS_WINDOW)

        self.runs = RunLengthWindow()
        self.count = 0
        self.value = 0.0
        self.initialized = False
        self.smoothed_increment: Decimal | None = None  # s(t), for tracing

    @property
    def period(self) -> int:
        return self.runs.period

    def update_raw(self, price: Decimal, increment_window: int) -> None:
        """
        Feed one price. `increment_window` is k, the number of increments to
        average - the fast MA's current N. Updates are skipped while k is
        undefined (fast MA still warming up) or while fewer than k+1 prices
        have been seen, so this MA always initializes after the fast one.
        """
        self._prices.append(price)
        self.count += 1

        if increment_window <= 0 or len(self._prices) <= increment_window:
            return

        self.smoothed_increment = (
            self._prices[-1] - self._prices[-1 - increment_window]
        ) / increment_window

        n = self.runs.update(_sign(self.smoothed_increment))
        if n <= 0 or n > len(self._prices):
            return

        window = list(self._prices)[-n:]
        self.value = float(sum(window) / n)
        self.initialized = True

    def state_repr(self) -> str:
        return self.runs.state_repr()


class MACrossoverConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    trade_size: Decimal = Decimal(10)
    heartbeat_interval_seconds: int = 30


class MACrossoverStrategy(Strategy):
    """
    Minimal moving-average crossover strategy:
      - Fast MA crosses above slow MA  -> go long
      - Fast MA crosses below slow MA  -> go short

    Neither MA has a period parameter: both windows are endogenous, derived
    from the series' own run dynamics. The fast MA runs on the raw
    increments, the slow MA on the moving average of those increments.

    A reversal is sent as one market order (close + entry combined), so
    only one commission is paid per direction change.
    """

    def __init__(self, config: MACrossoverConfig) -> None:
        super().__init__(config)

        self.fast_ma = RunLengthMovingAverage()
        self.slow_ma = SmoothedRunLengthMovingAverage()
        self.trade_size = Quantity.from_str(str(config.trade_size))

        self._fast_ma_ready_logged = False
        self._slow_ma_ready_logged = False
        self._prev_diff: float | None = None

        self._audit_rows: list[dict] = []
        self._pending_order_rows: dict[ClientOrderId, dict] = {}
        self._audit_file_path: str | None = None

        self._cumulative_realized_pnl = Decimal(0)
        self._last_trade_pnl: Decimal | None = None

    def on_start(self) -> None:
        instrument = self.cache.instrument(self.config.instrument_id)
        if instrument is None:
            self.log.error(f"Could not find instrument for {self.config.instrument_id}")
            self.stop()
            return

        # Neither MA is a Nautilus indicator, so on_bar updates both by hand
        # (first thing, matching the framework's update-then-handle order).
        self.subscribe_bars(self.config.bar_type)

        os.makedirs(AUDIT_DIRECTORY, exist_ok=True)
        self._audit_file_path = os.path.join(
            AUDIT_DIRECTORY,
            f"audit_{self.id}_{self.clock.utc_now():%Y%m%d_%H%M%S}.csv",
        )

        self.log.info(
            f"Started | instrument={self.config.instrument_id} "
            f"bar_type={self.config.bar_type} "
            f"fast_ma=endogenous(increment runs) "
            f"slow_ma=endogenous(smoothed-increment runs) "
            f"trade_size={self.trade_size} audit_file={self._audit_file_path}",
        )

        self._log_heartbeat(None)
        self.clock.set_timer(
            name=f"{self.id}-heartbeat",
            interval=timedelta(seconds=self.config.heartbeat_interval_seconds),
            callback=self._log_heartbeat,
        )

    def _log_heartbeat(self, event: TimeEvent | None) -> None:
        instrument_id = self.config.instrument_id
        net_position = self.portfolio.net_position(instrument_id)
        unrealized_pnl = self.portfolio.unrealized_pnl(instrument_id)
        realized_pnl = self.portfolio.realized_pnl(instrument_id)

        account = self.portfolio.account(IB_VENUE)
        balances = account.balances_total() if account is not None else {}

        fast_repr = (
            f"{self.fast_ma.value:.4f}(N={self.fast_ma.period})"
            if self.fast_ma.initialized
            else f"warming_up({self.fast_ma.count} bars, {self.fast_ma.state_repr()})"
        )
        slow_repr = (
            f"{self.slow_ma.value:.4f}(N={self.slow_ma.period})"
            if self.slow_ma.initialized
            else f"warming_up({self.slow_ma.count} bars, {self.slow_ma.state_repr()})"
        )

        self.log.info(
            f"[HEARTBEAT] position={net_position} "
            f"unrealized_pnl={unrealized_pnl} realized_pnl={realized_pnl} "
            f"fast_ma={fast_repr} slow_ma={slow_repr} balances={balances}",
        )

    def _trace_indicators(self, bar: Bar) -> tuple[float, str] | tuple[None, None]:
        if not self.fast_ma.initialized:
            self.log.debug(
                f"[INDICATOR] fast_ma warming up: {self.fast_ma.count} bars received, "
                f"waiting for one completed run of each sign - "
                f"{self.fast_ma.state_repr()} (last close={bar.close})",
            )
        elif not self._fast_ma_ready_logged:
            self.log.info(
                f"[INDICATOR] fast_ma initialized with endogenous window "
                f"N={self.fast_ma.period} value={self.fast_ma.value:.4f}",
            )
            self._fast_ma_ready_logged = True

        if not self.slow_ma.initialized:
            self.log.debug(
                f"[INDICATOR] slow_ma warming up: {self.slow_ma.count} bars received, "
                f"waiting for one completed run of each sign on the smoothed "
                f"increments - {self.slow_ma.state_repr()} (last close={bar.close})",
            )
        elif not self._slow_ma_ready_logged:
            self.log.info(
                f"[INDICATOR] slow_ma initialized with endogenous window "
                f"N={self.slow_ma.period} value={self.slow_ma.value:.4f}",
            )
            self._slow_ma_ready_logged = True

        if not (self.fast_ma.initialized and self.slow_ma.initialized):
            return None, None

        diff = self.fast_ma.value - self.slow_ma.value
        trend = "no_cross"
        if self._prev_diff is not None:
            if self._prev_diff <= 0 < diff:
                trend = "CROSSED_UP"
            elif self._prev_diff >= 0 > diff:
                trend = "CROSSED_DOWN"

        self.log.info(
            f"[INDICATOR] bar_close={bar.close} fast_ma={self.fast_ma.value:.4f} "
            f"(N={self.fast_ma.period}) slow_ma={self.slow_ma.value:.4f} "
            f"(N={self.slow_ma.period}) diff={diff:.4f} trend={trend}",
        )
        self._prev_diff = diff
        return diff, trend

    def _new_audit_row(self, timestamp: str) -> dict:
        """
        Build a row pre-filled with the fields that always reflect current
        strategy/account state, regardless of what triggered the row.
        """
        net_position = self.portfolio.net_position(self.config.instrument_id)
        unrealized = self.portfolio.unrealized_pnl(self.config.instrument_id)
        return {
            "timestamp": timestamp,
            "bar_price": "",
            "short_ma": "",
            "n_fast": "",
            "runs_fast": "",
            "long_ma": "",
            "n_slow": "",
            "runs_slow": "",
            "ma_diff": "",
            "trend": "",
            "trade": "",
            "position": str(net_position),
            "unrealized_pnl": str(unrealized) if unrealized is not None else "",
            "last_trade_pnl": str(self._last_trade_pnl) if self._last_trade_pnl is not None else "",
            "cumulative_pnl": str(self._cumulative_realized_pnl),
            "reject_reason": "",
            "note": "",
        }

    def on_bar(self, bar: Bar) -> None:
        # Both MAs are updated by hand, fast first: the slow MA averages the
        # increments over the fast MA's current endogenous window.
        close = bar.close.as_decimal()
        self.fast_ma.update_raw(close)
        self.slow_ma.update_raw(close, self.fast_ma.period)

        diff, trend = self._trace_indicators(bar)

        row = self._new_audit_row(unix_nanos_to_dt(bar.ts_event).isoformat())
        row["bar_price"] = str(bar.close)
        row["short_ma"] = f"{self.fast_ma.value:.5f}" if self.fast_ma.initialized else ""
        row["n_fast"] = str(self.fast_ma.period) if self.fast_ma.initialized else ""
        row["runs_fast"] = self.fast_ma.state_repr()
        row["long_ma"] = f"{self.slow_ma.value:.5f}" if self.slow_ma.initialized else ""
        row["n_slow"] = str(self.slow_ma.period) if self.slow_ma.initialized else ""
        row["runs_slow"] = self.slow_ma.state_repr()
        row["ma_diff"] = f"{diff:.5f}" if diff is not None else ""
        row["trend"] = trend or ""
        self._audit_rows.append(row)

        if not self.fast_ma.initialized or not self.slow_ma.initialized:
            return

        fast = self.fast_ma.value
        slow = self.slow_ma.value
        net_position = self.portfolio.net_position(self.config.instrument_id)

        if fast > slow and net_position <= 0:
            self.log.info("Fast MA crossed above slow MA -> BUY")
            self._reverse_to(OrderSide.BUY, row)
        elif fast < slow and net_position >= 0:
            self.log.info("Fast MA crossed below slow MA -> SELL")
            self._reverse_to(OrderSide.SELL, row)

    def _closable_quantity(self) -> Decimal:
        """
        Absolute size of the open position, rounded to a whole unit.

        IDEALPRO rejects fractional FX order quantities, but the position
        size Nautilus tracks can carry a fractional remainder (observed:
        EUR-denominated commissions getting folded into position quantity
        instead of staying purely a cost), so an order sized off that exact
        fractional net position gets rejected by IB and leaves the strategy
        stuck. Rounding to the nearest whole unit may leave <1 unit of
        residual dust, which is negligible at this trade size.
        """
        net_position = self.portfolio.net_position(self.config.instrument_id)
        if net_position == 0:
            return Decimal(0)

        qty = Decimal(str(abs(net_position))).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return qty if qty > 0 else Decimal(0)

    def _flatten_position(self) -> None:
        """
        Close the current position with a manually-sized market order
        (used on shutdown; reversals go through _reverse_to instead).
        """
        close_qty = self._closable_quantity()
        if close_qty <= 0:
            return

        net_position = self.portfolio.net_position(self.config.instrument_id)
        close_side = OrderSide.BUY if net_position < 0 else OrderSide.SELL
        order = self.order_factory.market(
            instrument_id=self.config.instrument_id,
            order_side=close_side,
            quantity=Quantity.from_str(str(close_qty)),
        )
        self.submit_order(order)

    def _reverse_to(self, side: OrderSide, row: dict) -> None:
        """
        Move to a `trade_size` position on `side` with a SINGLE market order.

        When a position is open on the opposite side, closing it and opening
        the new one are merged into one order of |current position| +
        trade_size, so IB charges one commission (and one bid/ask crossing)
        instead of two. The resulting net position is trade_size on `side`.

        No client-side cash pre-check: IB's account-summary API (as used by
        nautilus_trader's adapter) only reports totals in the account's base
        currency (EUR here), never a real per-currency free-cash figure for
        USD, so there is no reliable way to size against it from this side.
        If the account genuinely lacks the funds, IB will reject the order
        and on_order_rejected records that in the audit trail.
        """
        net_position = self.portfolio.net_position(self.config.instrument_id)
        opposite_open = (side == OrderSide.BUY and net_position < 0) or (
            side == OrderSide.SELL and net_position > 0
        )
        close_qty = self._closable_quantity() if opposite_open else Decimal(0)
        order_qty = close_qty + self.config.trade_size

        order = self.order_factory.market(
            instrument_id=self.config.instrument_id,
            order_side=side,
            quantity=Quantity.from_str(str(order_qty)),
        )

        sign = "+" if side == OrderSide.BUY else "-"
        row["trade"] = f"{sign}{order_qty}"
        if close_qty > 0:
            row["note"] = (
                f"REVERSAL single order: close={close_qty} + entry={self.config.trade_size}"
            )
            self.log.info(
                f"Reversing in one order: close {close_qty} + enter "
                f"{self.config.trade_size} = {order_qty} {side}",
            )
        self._pending_order_rows[order.client_order_id] = row
        self.submit_order(order)

    def _write_audit_csv(self) -> None:
        if self._audit_file_path is None or not self._audit_rows:
            self.log.info("[AUDIT] No bars processed - nothing to write")
            return

        fieldnames = [
            "timestamp",
            "bar_price",
            "short_ma",
            "n_fast",
            "runs_fast",
            "long_ma",
            "n_slow",
            "runs_slow",
            "ma_diff",
            "trend",
            "trade",
            "position",
            "unrealized_pnl",
            "last_trade_pnl",
            "cumulative_pnl",
            "reject_reason",
            "note",
        ]
        with open(self._audit_file_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(self._audit_rows)

        self.log.info(
            f"[AUDIT] Wrote {len(self._audit_rows)} rows to {self._audit_file_path}",
        )

    def on_stop(self) -> None:
        self.clock.cancel_timer(f"{self.id}-heartbeat")
        self._flatten_position()
        self.unsubscribe_bars(self.config.bar_type)
        self._write_audit_csv()

    # ------------------------------------------------------------------
    # Order lifecycle tracing
    # ------------------------------------------------------------------
    def on_order_filled(self, event: OrderFilled) -> None:
        self.log.info(
            f"[ORDER FILLED] {event.order_side} {event.last_qty} @ {event.last_px} "
            f"commission={event.commission} client_order_id={event.client_order_id} "
            f"position_id={event.position_id}",
        )
        self._pending_order_rows.pop(event.client_order_id, None)

    def on_order_rejected(self, event: OrderRejected) -> None:
        self.log.warning(
            f"[ORDER REJECTED] client_order_id={event.client_order_id} reason={event.reason}",
        )

        row = self._pending_order_rows.pop(event.client_order_id, None)
        if row is not None:
            row["reject_reason"] = event.reason
        else:
            # Rejection for an order not tied to a bar row (e.g. a flatten order).
            fallback_row = self._new_audit_row(self.clock.utc_now().isoformat())
            fallback_row["reject_reason"] = f"{event.reason} (client_order_id={event.client_order_id})"
            self._audit_rows.append(fallback_row)

    def on_order_canceled(self, event: OrderCanceled) -> None:
        self.log.info(f"[ORDER CANCELED] client_order_id={event.client_order_id}")

    # ------------------------------------------------------------------
    # Position lifecycle tracing
    # ------------------------------------------------------------------
    def on_position_opened(self, event: PositionOpened) -> None:
        self.log.info(
            f"[POSITION OPENED] side={event.side} qty={event.quantity} "
            f"avg_px_open={event.avg_px_open}",
        )

    def on_position_changed(self, event: PositionChanged) -> None:
        self.log.info(
            f"[POSITION CHANGED] side={event.side} qty={event.quantity} "
            f"avg_px_open={event.avg_px_open} realized_pnl={event.realized_pnl} "
            f"unrealized_pnl={event.unrealized_pnl}",
        )

    def on_position_closed(self, event: PositionClosed) -> None:
        trade_pnl = event.realized_pnl.as_decimal() if event.realized_pnl is not None else Decimal(0)
        self._last_trade_pnl = trade_pnl
        self._cumulative_realized_pnl += trade_pnl

        self.log.info(
            f"[POSITION CLOSED] avg_px_open={event.avg_px_open} "
            f"avg_px_close={event.avg_px_close} trade_pnl={trade_pnl} "
            f"cumulative_pnl={self._cumulative_realized_pnl}",
        )

        row = self._new_audit_row(self.clock.utc_now().isoformat())
        row["note"] = f"POSITION_CLOSED avg_open={event.avg_px_open} avg_close={event.avg_px_close}"
        self._audit_rows.append(row)


def main() -> None:
    contract = IBContract(
        secType=SEC_TYPE,
        symbol=BASE_CURRENCY,
        exchange=EXCHANGE,
        currency=QUOTE_CURRENCY,
    )

    instrument_provider_config = InteractiveBrokersInstrumentProviderConfig(
        load_contracts=frozenset([contract]),
    )

    data_client_config = InteractiveBrokersDataClientConfig(
        ibg_host=IBG_HOST,
        ibg_port=IBG_PORT,
        ibg_client_id=IBG_CLIENT_ID,
        instrument_provider=instrument_provider_config,
    )

    exec_client_config = InteractiveBrokersExecClientConfig(
        ibg_host=IBG_HOST,
        ibg_port=IBG_PORT,
        ibg_client_id=IBG_CLIENT_ID,
        account_id=IB_ACCOUNT_ID,
        instrument_provider=instrument_provider_config,
    )

    node_config = TradingNodeConfig(
        trader_id="PAPER-TRADER-001",
        logging=LoggingConfig(
            log_level=LOG_LEVEL_CONSOLE,
            log_level_file=LOG_LEVEL_FILE,
            log_directory=LOG_DIRECTORY,
        ),
        data_clients={IB: data_client_config},
        exec_clients={IB: exec_client_config},
    )

    bar_type = BarType.from_str(f"{INSTRUMENT_ID}-{BAR_SPEC}-{BAR_AGGREGATION_SOURCE}")

    strategy_config = MACrossoverConfig(
        instrument_id=INSTRUMENT_ID,
        bar_type=bar_type,
        trade_size=TRADE_SIZE,
        heartbeat_interval_seconds=HEARTBEAT_INTERVAL_SECONDS,
    )
    strategy = MACrossoverStrategy(config=strategy_config)

    node = TradingNode(config=node_config)
    node.trader.add_strategy(strategy)

    node.add_data_client_factory(IB, InteractiveBrokersLiveDataClientFactory)
    node.add_exec_client_factory(IB, InteractiveBrokersLiveExecClientFactory)
    node.build()

    try:
        node.run()
    except KeyboardInterrupt:
        # nautilus_trader's graceful-shutdown signal handler is a no-op on
        # Windows (see nautilus_trader.system.kernel: `_setup_loop()` is
        # skipped when platform.system() == "Windows"), so Ctrl-C surfaces
        # here as a plain KeyboardInterrupt instead of going through
        # node.stop() on its own. Call it explicitly so Strategy.on_stop()
        # still runs (flattening the position and writing the audit CSV)
        # before disposal.
        node.stop()
    finally:
        node.dispose()


if __name__ == "__main__":
    main()
