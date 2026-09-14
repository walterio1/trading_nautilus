"""Las clases de media endogena de `ibkr_paper_ma_crossover.py`, recortadas
LITERALMENTE de ese fichero (sin una linea cambiada) para poder usarlas sin
nautilus_trader instalado. No reimplementar nada aqui: si el motor cambia,
se vuelve a recortar."""
from collections import deque
from decimal import Decimal


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


