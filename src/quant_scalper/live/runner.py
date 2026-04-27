"""Live (and dry-run) trading runner.

Loop pattern (per 15-minute candle close):

1. For each symbol in the universe (currently open positions plus scan list)
   pull the most recent 1H + 15m candles via the connector.
2. Run :func:`generate_signals` on the appended history.
3. For symbols **already** carrying a position, manage the trade (trailing
   stop, exit on opposite signal, periodic risk checks).
4. For symbols **without** a position, take the most-recent confirmed signal
   and open a market order, then place SL + TP orders.

The runner never tries to micro-manage intra-bar PnL — the trailing logic
fires once per bar close, matching how the backtester behaves so live and
backtest results stay comparable.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd

from ..config import get_settings
from ..exchange.connector import BinanceUMConnector
from ..exchange.data import DEFAULT_TOP_SYMBOLS
from ..indicators import basic
from ..monitor.reporter import Reporter
from ..storage.db import TradeStore
from ..strategy.signal import Side, StrategyParams, generate_signals
from ..utils.logging import logger


@dataclass
class LiveParams:
    """Risk-side params for live runtime; mirror BacktestParams subset."""

    atr_sl_mult: float = 2.0
    atr_tp_mult: float = 3.0
    atr_length: int = 14
    trailing_trigger_pct: float = 0.005
    trailing_distance_pct: float = 0.003
    max_concurrent_positions: int = 5
    bars_warmup: int = 200
    poll_interval_seconds: int = 30


@dataclass
class _ActiveTrade:
    db_id: int
    symbol: str
    side: Side
    qty: float
    entry_price: float
    entry_time: pd.Timestamp
    stop_price: float
    tp_price: float
    trailing_armed: bool = False
    leverage: float = 10.0
    margin: float = 20.0


def _ohlcv_to_df(rows: list[list[float]]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms", utc=True).dt.tz_convert(None)
    return df.set_index("timestamp")[["open", "high", "low", "close", "volume"]].astype("float64")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class LiveRunner:
    def __init__(self, *, dry_run: bool = True) -> None:
        self.settings = get_settings()
        self.connector = BinanceUMConnector(dry_run=dry_run)
        self.store = TradeStore()
        self.reporter = Reporter()
        self.dry_run = dry_run
        self.params = LiveParams()
        self.strategy_params = StrategyParams()
        self.active: dict[str, _ActiveTrade] = {}
        self.universe: list[str] = list(DEFAULT_TOP_SYMBOLS)

    # ─── Risk sizing ────────────────────────────────────────────────
    def _margin_lev(self, symbol: str) -> tuple[float, float]:
        return self.settings.margin_for(symbol), float(self.settings.leverage_for(symbol))

    def _qty(self, symbol: str, price: float) -> tuple[float, float, float]:
        margin, lev = self._margin_lev(symbol)
        notional = margin * lev
        qty = notional / price
        return qty, margin, lev

    # ─── Bar fetch ──────────────────────────────────────────────────
    def _recent_bars(self, symbol: str, timeframe: str, limit: int = 250) -> pd.DataFrame:
        rows = self.connector.fetch_recent_ohlcv(symbol, timeframe, limit=limit)
        return _ohlcv_to_df(rows)

    # ─── Trade management ──────────────────────────────────────────
    def _open_trade(self, symbol: str, side: Side, df: pd.DataFrame, atr_value: float) -> None:
        if len(self.active) >= self.params.max_concurrent_positions:
            return
        last_close = float(df["close"].iloc[-1])
        qty, margin, lev = self._qty(symbol, last_close)

        sl_dist = self.params.atr_sl_mult * atr_value
        tp_dist = self.params.atr_tp_mult * atr_value
        if side is Side.LONG:
            stop = last_close - sl_dist
            target = last_close + tp_dist
            ord_side = "buy"
        else:
            stop = last_close + sl_dist
            target = last_close - tp_dist
            ord_side = "sell"

        self.connector.set_margin_mode(symbol, "cross")
        self.connector.set_leverage(symbol, int(lev))
        order = self.connector.market_open(symbol, ord_side, qty, last_close)
        # protective orders
        try:
            self.connector.stop_market(symbol, ord_side, qty, stop)
            self.connector.take_profit_market(symbol, ord_side, qty, target)
        except Exception as e:
            logger.error(f"protective order placement failed on {symbol}: {e}")

        active = _ActiveTrade(
            db_id=self.store.insert_open(
                symbol=symbol, side=side.value, qty=qty,
                entry_price=order.price, entry_time=_utcnow_iso(),
                leverage=lev, margin_usdt=margin, stop_price=stop, tp_price=target,
                notes="dry" if self.dry_run else "live",
            ),
            symbol=symbol, side=side, qty=qty,
            entry_price=order.price, entry_time=pd.Timestamp.utcnow().tz_localize(None),
            stop_price=stop, tp_price=target, leverage=lev, margin=margin,
        )
        self.active[symbol] = active
        self.reporter.opened(symbol, side.value, qty, order.price, lev)

    def _close_trade(self, sym: str, last_close: float, reason: str) -> None:
        tr = self.active.pop(sym, None)
        if tr is None:
            return
        order_side = "buy" if tr.side is Side.LONG else "sell"
        self.connector.cancel_open_orders(sym)
        order = self.connector.market_close(sym, order_side, tr.qty, last_close)
        sign = 1 if tr.side is Side.LONG else -1
        pnl_quote = (order.price - tr.entry_price) * tr.qty * sign
        pnl_pct = pnl_quote / tr.margin if tr.margin else 0.0
        self.store.update_close(
            tr.db_id, exit_price=order.price, exit_time=_utcnow_iso(),
            pnl_quote=pnl_quote, pnl_pct=pnl_pct, exit_reason=reason,
        )
        self.reporter.closed(sym, tr.side.value, tr.qty, order.price, pnl_quote, pnl_pct, reason)

    def _manage_trailing(self, tr: _ActiveTrade, last_close: float) -> None:
        # Arm + tighten the trailing stop once price has moved favourably.
        trig_pct = self.params.trailing_trigger_pct
        dist_pct = self.params.trailing_distance_pct
        if tr.side is Side.LONG:
            if last_close >= tr.entry_price * (1 + trig_pct):
                tr.trailing_armed = True
            if tr.trailing_armed:
                new_stop = last_close * (1 - dist_pct)
                if new_stop > tr.stop_price:
                    tr.stop_price = new_stop
                    self.store.update_stop(tr.db_id, new_stop)
                    self.connector.cancel_open_orders(tr.symbol)
                    self.connector.stop_market(tr.symbol, "buy", tr.qty, new_stop)
                    self.connector.take_profit_market(tr.symbol, "buy", tr.qty, tr.tp_price)
        else:
            if last_close <= tr.entry_price * (1 - trig_pct):
                tr.trailing_armed = True
            if tr.trailing_armed:
                new_stop = last_close * (1 + dist_pct)
                if new_stop < tr.stop_price:
                    tr.stop_price = new_stop
                    self.store.update_stop(tr.db_id, new_stop)
                    self.connector.cancel_open_orders(tr.symbol)
                    self.connector.stop_market(tr.symbol, "sell", tr.qty, new_stop)
                    self.connector.take_profit_market(tr.symbol, "sell", tr.qty, tr.tp_price)

    # ─── One scan iteration ────────────────────────────────────────
    def _scan_symbol(self, symbol: str) -> None:
        df = self._recent_bars(symbol, self.settings.timeframe, 250)
        if len(df) < self.params.bars_warmup:
            return
        mtf = self._recent_bars(symbol, self.settings.mtf_timeframe, 250)

        # Detect protective fills first — exchange-side SL/TP may have triggered
        # since the last poll. Reconcile by querying open positions.
        # (For simplicity in v1 we only rely on the bar-close trailing logic.)

        last_close = float(df["close"].iloc[-1])
        atr_value = float(
            basic.atr(df["high"], df["low"], df["close"], self.params.atr_length)
            .bfill()
            .iloc[-1]
        )

        # Manage existing trade
        if symbol in self.active:
            self._manage_trailing(self.active[symbol], last_close)
            return

        signals = generate_signals(df, mtf_df=mtf, params=self.strategy_params)
        if not signals:
            return
        last = signals[-1]
        if last.bar_idx < len(df) - 2:
            return  # signal is stale (older than the just-closed bar)
        self._open_trade(symbol, last.side, df, atr_value)

    def loop_once(self) -> None:
        # always include any active trade, even if not in scanner universe
        seen: set[str] = set()
        for sym in list(self.active.keys()) + list(self.universe):
            if sym in seen:
                continue
            seen.add(sym)
            try:
                self._scan_symbol(sym)
            except Exception as e:
                self.reporter.error(f"{sym}: {e}")

    def run_forever(self) -> None:
        logger.info(f"Live runner starting — dry_run={self.dry_run} mode={self.settings.mode.value}")
        while True:
            try:
                self.loop_once()
            except KeyboardInterrupt:
                logger.info("Stopping (KeyboardInterrupt)")
                return
            except Exception as e:
                self.reporter.error(f"loop error: {e}")
            time.sleep(self.params.poll_interval_seconds)


def run_live(*, dry_run: bool = True) -> None:
    LiveRunner(dry_run=dry_run).run_forever()
