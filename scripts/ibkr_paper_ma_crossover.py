
# comprobar que funciona con IB Gateway y TWS
# que lo enseñe en TWS
# sintetizar señal: mm con n endógena

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
from datetime import timedelta
from decimal import ROUND_DOWN
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
from nautilus_trader.indicators.averages import SimpleMovingAverage
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
from nautilus_trader.model.objects import Currency
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
FAST_MA_PERIOD = 2                # Minimum useful values for fast debugging:
SLOW_MA_PERIOD = 4                 #   both MAs initialize after a few bars
                                    #   instead of waiting for a real trend window
TRADE_SIZE = Decimal(20000)      # Ceiling quantity per order (FX base currency
                                 # units; IDEALPRO's typical minimum is 20,000).
                                 # Actual size may be reduced — see
                                 # LEVERAGE_SAFETY_BUFFER below.
HEARTBEAT_INTERVAL_SECONDS = 30  # How often to log account/position/indicator status

# ============================================================================
# CURRENCY / LEVERAGE SAFETY
# ============================================================================
# IB will reject an FX order with "would expose account to currency leverage"
# if it would require spending more of a currency than the account actually
# holds (e.g. buying EUR/USD needs free USD cash; a cash account funded only
# in EUR has none, so any BUY beyond what's covered by prior SELL proceeds is
# rejected regardless of size). To avoid that, every order is sized down (at
# submission time) to what the account can currently afford unlevered; if
# available cash rounds to zero the order is skipped instead of submitted.
LEVERAGE_SAFETY_BUFFER = Decimal("0.98")  # use at most 98% of free cash per order,
                                           # leaving headroom for spread/fees

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


class MACrossoverConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    fast_period: int = 10
    slow_period: int = 30
    trade_size: Decimal = Decimal(10)
    heartbeat_interval_seconds: int = 30


class MACrossoverStrategy(Strategy):
    """
    Minimal moving-average crossover strategy:
      - Fast MA crosses above slow MA  -> go long (flatten short first)
      - Fast MA crosses below slow MA  -> go short (flatten long first)
    """

    def __init__(self, config: MACrossoverConfig) -> None:
        super().__init__(config)

        self.fast_ma = SimpleMovingAverage(config.fast_period)
        self.slow_ma = SimpleMovingAverage(config.slow_period)
        self.trade_size = Quantity.from_str(str(config.trade_size))

        self._fast_ma_ready_logged = False
        self._slow_ma_ready_logged = False
        self._prev_diff: float | None = None

        self._base_currency = Currency.from_str(self.config.instrument_id.symbol.value.split("/")[0])
        self._quote_currency = Currency.from_str(self.config.instrument_id.symbol.value.split("/")[1])

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

        # Register indicators so they're fed automatically as bars arrive
        self.register_indicator_for_bars(self.config.bar_type, self.fast_ma)
        self.register_indicator_for_bars(self.config.bar_type, self.slow_ma)

        self.subscribe_bars(self.config.bar_type)

        os.makedirs(AUDIT_DIRECTORY, exist_ok=True)
        self._audit_file_path = os.path.join(
            AUDIT_DIRECTORY,
            f"audit_{self.id}_{self.clock.utc_now():%Y%m%d_%H%M%S}.csv",
        )

        self.log.info(
            f"Started | instrument={self.config.instrument_id} "
            f"bar_type={self.config.bar_type} "
            f"fast_ma={self.config.fast_period} slow_ma={self.config.slow_period} "
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
            f"{self.fast_ma.value:.4f}"
            if self.fast_ma.initialized
            else f"warming_up({self.fast_ma.count}/{self.fast_ma.period})"
        )
        slow_repr = (
            f"{self.slow_ma.value:.4f}"
            if self.slow_ma.initialized
            else f"warming_up({self.slow_ma.count}/{self.slow_ma.period})"
        )

        self.log.info(
            f"[HEARTBEAT] position={net_position} "
            f"unrealized_pnl={unrealized_pnl} realized_pnl={realized_pnl} "
            f"fast_ma={fast_repr} slow_ma={slow_repr} balances={balances}",
        )

    def _trace_indicators(self, bar: Bar) -> tuple[float, str] | tuple[None, None]:
        if not self.fast_ma.initialized:
            self.log.debug(
                f"[INDICATOR] fast_ma warming up: {self.fast_ma.count}/{self.fast_ma.period} "
                f"bars received (last close={bar.close})",
            )
        elif not self._fast_ma_ready_logged:
            self.log.info(
                f"[INDICATOR] fast_ma initialized period={self.fast_ma.period} "
                f"value={self.fast_ma.value:.4f}",
            )
            self._fast_ma_ready_logged = True

        if not self.slow_ma.initialized:
            self.log.debug(
                f"[INDICATOR] slow_ma warming up: {self.slow_ma.count}/{self.slow_ma.period} "
                f"bars received (last close={bar.close})",
            )
        elif not self._slow_ma_ready_logged:
            self.log.info(
                f"[INDICATOR] slow_ma initialized period={self.slow_ma.period} "
                f"value={self.slow_ma.value:.4f}",
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
            f"slow_ma={self.slow_ma.value:.4f} diff={diff:.4f} trend={trend}",
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
            "long_ma": "",
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
        diff, trend = self._trace_indicators(bar)

        row = self._new_audit_row(unix_nanos_to_dt(bar.ts_event).isoformat())
        row["bar_price"] = str(bar.close)
        row["short_ma"] = f"{self.fast_ma.value:.5f}" if self.fast_ma.initialized else ""
        row["long_ma"] = f"{self.slow_ma.value:.5f}" if self.slow_ma.initialized else ""
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
            self._flatten_and_enter(OrderSide.BUY, bar, row)
        elif fast < slow and net_position >= 0:
            self.log.info("Fast MA crossed below slow MA -> SELL")
            self._flatten_and_enter(OrderSide.SELL, bar, row)

    def _max_safe_quantity(self, side: OrderSide, ref_price: float) -> Decimal:
        """
        Cap order size to what the account can afford without going negative
        (leveraged) in the currency being spent, so IB won't reject the order.
        """
        account = self.portfolio.account(IB_VENUE)
        if account is None:
            return Decimal(0)

        if side == OrderSide.BUY:
            # Paying quote currency to receive base currency.
            free = account.balance_free(self._quote_currency)
            if free is None:
                return Decimal(0)
            max_qty = (free.as_decimal() / Decimal(str(ref_price))) * LEVERAGE_SAFETY_BUFFER
        else:
            # Paying (selling) base currency to receive quote currency.
            free = account.balance_free(self._base_currency)
            if free is None:
                return Decimal(0)
            max_qty = free.as_decimal() * LEVERAGE_SAFETY_BUFFER

        max_qty = max_qty.quantize(Decimal("1"), rounding=ROUND_DOWN)
        return max(Decimal(0), min(max_qty, self.config.trade_size))

    def _flatten_and_enter(self, side: OrderSide, bar: Bar, row: dict) -> None:
        if self.portfolio.is_net_long(self.config.instrument_id) or self.portfolio.is_net_short(
            self.config.instrument_id,
        ):
            self.close_all_positions(self.config.instrument_id)

        safe_qty = self._max_safe_quantity(side, float(bar.close))
        if safe_qty <= 0:
            reason = (
                f"insufficient "
                f"{self._quote_currency if side == OrderSide.BUY else self._base_currency} "
                f"cash to enter {side.name} without leverage"
            )
            self.log.warning(f"[ORDER SKIPPED] side={side.name} reason={reason}")
            row["reject_reason"] = reason
            return

        quantity = Quantity.from_str(str(safe_qty))
        order = self.order_factory.market(
            instrument_id=self.config.instrument_id,
            order_side=side,
            quantity=quantity,
        )
        row["trade"] = f"{'+' if side == OrderSide.BUY else '-'}{safe_qty}"
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
            "long_ma",
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
        self.close_all_positions(self.config.instrument_id)
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
        fast_period=FAST_MA_PERIOD,
        slow_period=SLOW_MA_PERIOD,
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
    finally:
        node.dispose()


if __name__ == "__main__":
    main()
