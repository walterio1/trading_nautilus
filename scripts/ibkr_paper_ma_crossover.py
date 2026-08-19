# pedir un reminder de dóne estamos
# posible indicador de switch para fast+slow (A) - fast+superSlow (B) - slow-superSlow (C)
# cada una genera un bº en tendencia (el opuesto es reversión)
# tendremos 3 series de bº. Según sea una u otra >0 se hace el swithc
# >0 es delsde el último cruce slow vs SuperSlow (punto común de inicio acumular bº como indicador)
# otro criterio: segmentar estados: min(A, B, C) > 0; max(A, B, C) < 0; 
# A > 0 & B < 0 & C < 0; A < 0 & B > 0 & C < 0; A < 0 & B < 0 & C > 0
# en qué estado gana dinero A, B o C? (o ninguno) (o todos)
# otra opción: los bºs pueden ser desde ese trade (A, B, C tienen distintas duraciones) (punto no común)

# probar barras 15 min con las mm endógenas actuales
# test fast vs slow y fast vs superSlow y slow vs superSlow
# para el test, meter datos de fuera (minuto? día? Hora?)

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
# THREE moving averages are computed on every bar, none of them with a period
# parameter: every window is endogenous, derived from the series' own run
# dynamics. The fast one reads the runs of the raw increments
# (RunLengthMovingAverage); the other two are built on top of its window.
#
#   fast        N_fast, run lengths of the raw increments
#   slow        N_slow, per SLOW_WINDOW_MODE below
#   super_slow  N_super ~ 2^L * N_fast, the lagged rule iterated L levels
#
SLOW_WINDOW_MODE = "smoothed_runs"   # How the SLOW MA derives its window:
# "smoothed_runs" -> SmoothedRunLengthMovingAverage: a second run-length pass,
#                    run on the moving average of the increments over the fast
#                    MA's own window.
# "lagged_fast"   -> LaggedWindowMovingAverage(levels=1): no second run-length
#                    pass at all, N_slow(t) = N_fast(t) + N_fast(t - N_fast(t)).

SUPER_SLOW_LEVELS = 3            # Depth of the SUPER_SLOW MA: the lagged rule
                                 # iterated L times, each level reading the one
                                 # below it. Depth is an integer count of
                                 # levels, not a length, so no exogenous period
                                 # sneaks back in. Each level roughly doubles
                                 # the window (3 levels ~ 8x the fast one) and
                                 # warm-up grows on the same 2^L scale.

# Which pair actually trades. All three MAs are computed and written to the
# audit CSV regardless, so one run's CSV supports comparing every pairing
# offline; this only decides which crossover submits orders live.
CROSSOVER_PAIR = "fast_vs_slow"
# "fast_vs_slow"        -> the live mechanism
# "fast_vs_super_slow"  -> widest separation, fewest signals
# "slow_vs_super_slow"  -> both legs smoothed, no raw-increment leg
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


class LaggedWindowMovingAverage:
    """
    Slow MA, alternative construction: the window is read straight off a
    window history, with no second run-length pass.

    One level (`levels=1`) applies the rule to the fast MA's own history:

        N_1(t) = N_fast(t) + N_fast(t - N_fast(t))

    Take the window as it stands now, look back exactly that many bars, and
    add whatever the window was at that point.

    Example - N_fast history 8, 7, 6, 5, 6, 7, 5, 4 (last value = now):
        now    : N_fast = 4, four bars back N_fast was 5  ->  N_1 = 9
        prev   : N_fast = 5, five bars back N_fast was 7  ->  N_1 = 12

    With `levels=L` the same rule is iterated, each level reading the level
    below it (level 0 being the fast MA itself):

        N_L(t) = N_{L-1}(t) + N_{L-1}(t - N_{L-1}(t))

    Every level keeps its own per-bar history, so the lag at level L is that
    level's own current window, measured in bars. The final window is the
    top level's value.

    Properties worth being explicit about:

    1. Every defined window is >= 2, so N_L(t) >= N_{L-1}(t) + 2 whenever it
       is defined: each level is strictly slower than the one below it, in
       every bar, not just on average.
    2. Each level roughly DOUBLES the window (it adds a past value of the
       same series to the current one), so N_L ~ 2^L * N_fast. This is the
       mechanism for reaching a much larger N without ever fixing a period:
       the extra depth is an integer count of levels, not a length.
    3. The price of that depth is warm-up. Level L cannot resolve until its
       lookback lands on a bar where level L-1 was already defined, so the
       bars needed before the MA initializes also grow like 2^L * N_fast.
    4. The lag IS the current window at every level, so the memory still
       scales with the series' own run structure rather than a constant.

    Base series : N_fast(t), one value per bar (0 while the fast MA is still
                  warming up - those bars are recorded so the lag stays
                  measured in bars, but are never used as a lagged value)
    Window      : N_L(t) per the recursion above, capped at
                  MAX_ENDOGENOUS_WINDOW
    Value       : mean of the last N_L(t) PRICES, ending at t - same as the
                  other MAs, so the crossover stays in price units.
    """

    def __init__(self, levels: int = 1) -> None:
        if levels < 1:
            raise ValueError(f"levels must be >= 1, got {levels}")

        self.levels = levels
        self._prices: deque[Decimal] = deque(maxlen=MAX_ENDOGENOUS_WINDOW)
        # One history per level, level 0 being the fast MA's own window.
        # Each is one slot longer than the largest reachable lag: a lookup
        # needs N(t) plus the entry N(t) bars before it.
        self._histories: list[deque[int]] = [
            deque(maxlen=MAX_ENDOGENOUS_WINDOW + 1) for _ in range(levels + 1)
        ]

        self.count = 0
        self.value = 0.0
        self.initialized = False
        self.period = 0
        self.chain: list[int] = [0] * (levels + 1)  # Per-level window, for tracing

    @staticmethod
    def _next_level(history: deque[int]) -> int:
        """
        Apply the rule once to `history` (which already includes the current
        bar). Returns 0 when the level is still undefined.
        """
        current = history[-1]
        if current <= 0 or len(history) <= current:
            return 0  # Level below undefined, or history does not reach back

        # Index -1 is the current bar, so t - N(t) sits at -1 - N(t).
        lagged = history[-1 - current]
        if lagged <= 0:
            return 0  # The window was still undefined that far back

        return min(current + lagged, MAX_ENDOGENOUS_WINDOW)

    def update_raw(self, price: Decimal, increment_window: int) -> None:
        """
        Feed one price plus the fast MA's current window N_fast(t). Every bar
        appends one entry to every level, including the warm-up bars where a
        level is still 0, so all lags stay measured in bars.
        """
        self._prices.append(price)
        self.count += 1

        self._histories[0].append(increment_window)
        self.chain[0] = increment_window
        for level in range(1, self.levels + 1):
            n = self._next_level(self._histories[level - 1])
            self._histories[level].append(n)
            self.chain[level] = n

        n = self.chain[self.levels]
        if n <= 0 or n > len(self._prices):
            return  # Top level undefined, or not enough prices to average yet

        self.period = n
        window = list(self._prices)[-n:]
        self.value = float(sum(window) / n)
        self.initialized = True

    def state_repr(self) -> str:
        chain = " -> ".join(
            f"N_fast={n}" if level == 0 else f"L{level}={n}"
            for level, n in enumerate(self.chain)
        )
        return f"{chain} N={self.period}"


SLOW_MA_BUILDERS = {
    "smoothed_runs": lambda config: SmoothedRunLengthMovingAverage(),
    "lagged_fast": lambda config: LaggedWindowMovingAverage(levels=1),
}

# Which two of the three MAs the crossover trades. The first name is the
# faster leg, the second the slower one: the strategy goes long when the
# faster leg sits above the slower one and short when it sits below.
# All three MAs are computed and audited on every bar regardless, so a single
# run's CSV supports comparing all three pairings offline.
CROSSOVER_PAIRS = {
    "fast_vs_slow": ("fast_ma", "slow_ma"),
    "fast_vs_super_slow": ("fast_ma", "super_slow_ma"),
    "slow_vs_super_slow": ("slow_ma", "super_slow_ma"),
}

MA_LABELS = {"fast_ma": "fast", "slow_ma": "slow", "super_slow_ma": "super_slow"}


class MACrossoverConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    trade_size: Decimal = Decimal(10)
    heartbeat_interval_seconds: int = 30
    slow_window_mode: str = "smoothed_runs"
    super_slow_levels: int = 3
    crossover_pair: str = "fast_vs_slow"


class MACrossoverStrategy(Strategy):
    """
    Moving-average crossover strategy over three endogenous-window MAs.

    Three MAs are maintained on every bar:
      fast_ma        run lengths of the raw increments               -> N_fast
      slow_ma        `slow_window_mode`, built on top of N_fast      -> N_slow
      super_slow_ma  the lagged rule iterated `super_slow_levels`
                     times, so N ~ 2^L * N_fast                      -> N_super

    `crossover_pair` picks which two of them actually trade:
      "fast_vs_slow"        (default - the live mechanism)
      "fast_vs_super_slow"
      "slow_vs_super_slow"

    The strategy goes long when the pair's faster leg is above its slower leg
    and short when it is below. All three MAs are traced and written to the
    audit CSV whichever pair is selected, so one run supports comparing all
    three pairings offline.

    No MA has a period parameter: every window is endogenous, derived from
    the series' own run dynamics.

    A reversal is sent as one market order (close + entry combined), so
    only one commission is paid per direction change.
    """

    def __init__(self, config: MACrossoverConfig) -> None:
        super().__init__(config)

        slow_ma_builder = SLOW_MA_BUILDERS.get(config.slow_window_mode)
        if slow_ma_builder is None:
            raise ValueError(
                f"Unknown slow_window_mode {config.slow_window_mode!r}, "
                f"expected one of {sorted(SLOW_MA_BUILDERS)}",
            )
        pair = CROSSOVER_PAIRS.get(config.crossover_pair)
        if pair is None:
            raise ValueError(
                f"Unknown crossover_pair {config.crossover_pair!r}, "
                f"expected one of {sorted(CROSSOVER_PAIRS)}",
            )

        self.fast_ma = RunLengthMovingAverage()
        self.slow_ma = slow_ma_builder(config)
        self.super_slow_ma = LaggedWindowMovingAverage(levels=config.super_slow_levels)
        self.trade_size = Quantity.from_str(str(config.trade_size))

        # The two legs that actually trade, resolved once at construction.
        self._faster_name, self._slower_name = pair

        self._ready_logged: set[str] = set()
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
            f"slow_ma=endogenous({self.config.slow_window_mode}) "
            f"super_slow_ma=endogenous(lagged x{self.super_slow_ma.levels}) "
            f"trading={self.config.crossover_pair} "
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

        reprs = " ".join(
            f"{name}={self._ma_repr(name, verbose=True)}" for name in MA_LABELS
        )
        self.log.info(
            f"[HEARTBEAT] position={net_position} "
            f"unrealized_pnl={unrealized_pnl} realized_pnl={realized_pnl} "
            f"trading={self.config.crossover_pair} {reprs} balances={balances}",
        )

    @property
    def faster_ma(self):
        """The traded pair's faster leg."""
        return getattr(self, self._faster_name)

    @property
    def slower_ma(self):
        """The traded pair's slower leg."""
        return getattr(self, self._slower_name)

    def _ma_repr(self, name: str, verbose: bool = False) -> str:
        ma = getattr(self, name)
        if ma.initialized:
            return f"{ma.value:.4f}(N={ma.period})"
        if verbose:
            return f"warming_up({ma.count} bars, {ma.state_repr()})"
        return "warming_up"

    def _trace_indicators(self, bar: Bar) -> tuple[float, str] | tuple[None, None]:
        # Trace all three MAs, not just the traded pair: the whole point of
        # computing them together is being able to compare pairings offline.
        for name in MA_LABELS:
            ma = getattr(self, name)
            if not ma.initialized:
                self.log.debug(
                    f"[INDICATOR] {name} warming up: {ma.count} bars received, "
                    f"N still undefined - {ma.state_repr()} (last close={bar.close})",
                )
            elif name not in self._ready_logged:
                self.log.info(
                    f"[INDICATOR] {name} initialized with endogenous window "
                    f"N={ma.period} value={ma.value:.4f}",
                )
                self._ready_logged.add(name)

        if not (self.faster_ma.initialized and self.slower_ma.initialized):
            return None, None

        diff = self.faster_ma.value - self.slower_ma.value
        trend = "no_cross"
        if self._prev_diff is not None:
            if self._prev_diff <= 0 < diff:
                trend = "CROSSED_UP"
            elif self._prev_diff >= 0 > diff:
                trend = "CROSSED_DOWN"

        others = " ".join(
            f"{name}={self._ma_repr(name)}"
            for name in MA_LABELS
            if name not in (self._faster_name, self._slower_name)
        )
        self.log.info(
            f"[INDICATOR] bar_close={bar.close} "
            f"{self._faster_name}={self.faster_ma.value:.4f}(N={self.faster_ma.period}) "
            f"{self._slower_name}={self.slower_ma.value:.4f}(N={self.slower_ma.period}) "
            f"diff={diff:.4f} trend={trend} | untraded: {others}",
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
            "pair": self.config.crossover_pair,
            "fast_ma": "",
            "n_fast": "",
            "runs_fast": "",
            "slow_ma": "",
            "n_slow": "",
            "runs_slow": "",
            "super_slow_ma": "",
            "n_super_slow": "",
            "runs_super_slow": "",
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
        # All three MAs are updated by hand, fast first: both slower ones are
        # built on the fast MA's endogenous window from this same bar. They
        # are updated whichever pair trades, so the audit CSV always carries
        # the full picture.
        close = bar.close.as_decimal()
        self.fast_ma.update_raw(close)
        self.slow_ma.update_raw(close, self.fast_ma.period)
        self.super_slow_ma.update_raw(close, self.fast_ma.period)

        diff, trend = self._trace_indicators(bar)

        row = self._new_audit_row(unix_nanos_to_dt(bar.ts_event).isoformat())
        row["bar_price"] = str(bar.close)
        for name, prefix in (
            ("fast_ma", "fast"),
            ("slow_ma", "slow"),
            ("super_slow_ma", "super_slow"),
        ):
            ma = getattr(self, name)
            row[name] = f"{ma.value:.5f}" if ma.initialized else ""
            row[f"n_{prefix}"] = str(ma.period) if ma.initialized else ""
            row[f"runs_{prefix}"] = ma.state_repr()
        row["ma_diff"] = f"{diff:.5f}" if diff is not None else ""
        row["trend"] = trend or ""
        self._audit_rows.append(row)

        # Only the traded pair gates execution; a slower MA still warming up
        # is written to the CSV but does not hold up an unrelated pairing.
        if not (self.faster_ma.initialized and self.slower_ma.initialized):
            return

        faster = self.faster_ma.value
        slower = self.slower_ma.value
        net_position = self.portfolio.net_position(self.config.instrument_id)

        if faster > slower and net_position <= 0:
            self.log.info(
                f"{self._faster_name} crossed above {self._slower_name} -> BUY",
            )
            self._reverse_to(OrderSide.BUY, row)
        elif faster < slower and net_position >= 0:
            self.log.info(
                f"{self._faster_name} crossed below {self._slower_name} -> SELL",
            )
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
            "pair",
            "fast_ma",
            "n_fast",
            "runs_fast",
            "slow_ma",
            "n_slow",
            "runs_slow",
            "super_slow_ma",
            "n_super_slow",
            "runs_super_slow",
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
        slow_window_mode=SLOW_WINDOW_MODE,
        super_slow_levels=SUPER_SLOW_LEVELS,
        crossover_pair=CROSSOVER_PAIR,
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
