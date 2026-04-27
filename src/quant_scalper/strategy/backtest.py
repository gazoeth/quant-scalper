"""Vectorised single-instrument backtester.

Design choices that prevent us from lying to ourselves:

* A signal generated at bar ``i`` is *entered at the next bar's open*
  (no look-ahead).
* Stop-loss and take-profit are evaluated bar-by-bar using
  ``high``/``low``. If both are touched in the same bar we assume
  the **stop** fills first (pessimistic).
* Trailing stop is updated on each bar AFTER checking the static SL.
* Per-fill slippage of ``slippage_bps`` and taker fee of ``fee_bps`` are
  applied to entry and exit; both decrement realised PnL.
* Position sizing is done in *quote currency* (USDT margin × leverage),
  so the realised PnL is reported in USDT.

The output is a list of trade dictionaries plus aggregated KPIs.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from ..indicators import basic
from .signal import Side, Signal


@dataclass
class BacktestParams:
    """Risk and execution params.

    Two stop modes are supported:

    * ``stop_mode = "pct"``: fixed percentage stops (``stop_loss_pct`` /
      ``take_profit_pct``).
    * ``stop_mode = "atr"``: stops priced as a multiple of ATR(14) at entry,
      adapting to current volatility.
    """

    stop_mode: str = "atr"
    stop_loss_pct: float = 0.008
    take_profit_pct: float = 0.015
    atr_sl_mult: float = 2.0
    atr_tp_mult: float = 3.0
    atr_length: int = 14

    trailing_trigger_pct: float = 0.005
    trailing_distance_pct: float = 0.003
    skip_entry_bar_stop: bool = True
    """Avoid the pathology where SL is hit by 15-minute noise on the same bar
    we enter; only check stops from the *next* bar onwards."""

    fee_bps: float = 4.0  # taker fee 0.04%
    slippage_bps: float = 2.0  # 0.02% each side
    margin_usdt: float = 20.0
    leverage: float = 10.0
    max_concurrent: int = 1  # per-symbol — cross-symbol caps live in scanner

    @property
    def fee_rate(self) -> float:
        return self.fee_bps / 10_000.0

    @property
    def slip_rate(self) -> float:
        return self.slippage_bps / 10_000.0

    @property
    def notional(self) -> float:
        return self.margin_usdt * self.leverage


@dataclass
class Trade:
    symbol: str
    side: str
    entry_idx: int
    exit_idx: int
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    exit_price: float
    qty: float
    pnl_quote: float
    pnl_pct: float
    exit_reason: str
    bars_held: int


@dataclass
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)

    def to_frame(self) -> pd.DataFrame:
        if not self.trades:
            return pd.DataFrame(
                columns=[
                    "symbol", "side", "entry_idx", "exit_idx", "entry_time",
                    "exit_time", "entry_price", "exit_price", "qty",
                    "pnl_quote", "pnl_pct", "exit_reason", "bars_held",
                ]
            )
        return pd.DataFrame([asdict(t) for t in self.trades])

    def kpis(self) -> dict:
        df = self.to_frame()
        n = len(df)
        if n == 0:
            return {
                "trades": 0, "wins": 0, "losses": 0,
                "win_rate": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
                "expectancy_quote": 0.0, "profit_factor": 0.0,
                "total_pnl_quote": 0.0, "max_drawdown_pct": 0.0,
                "avg_bars": 0.0,
            }
        wins = df[df["pnl_quote"] > 0]
        losses = df[df["pnl_quote"] <= 0]
        avg_win = float(wins["pnl_quote"].mean()) if not wins.empty else 0.0
        avg_loss = float(losses["pnl_quote"].mean()) if not losses.empty else 0.0
        gross_win = float(wins["pnl_quote"].sum())
        gross_loss = -float(losses["pnl_quote"].sum())
        equity = df["pnl_quote"].cumsum()
        peak = equity.cummax()
        dd = (equity - peak)
        max_dd = float(-dd.min()) if len(dd) else 0.0
        return {
            "trades": int(n),
            "wins": int(len(wins)),
            "losses": int(len(losses)),
            "win_rate": float(len(wins) / n),
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "expectancy_quote": float(df["pnl_quote"].mean()),
            "profit_factor": float(gross_win / gross_loss) if gross_loss > 0 else float("inf"),
            "total_pnl_quote": float(df["pnl_quote"].sum()),
            "max_drawdown_pct": float(max_dd / max(1.0, df["pnl_quote"].abs().sum())),
            "avg_bars": float(df["bars_held"].mean()),
        }


def _exit_price_inside_bar(
    bar_high: float,
    bar_low: float,
    bar_open: float,
    bar_close: float,
    stop: float,
    target: float,
    side: Side,
) -> tuple[float, str] | None:
    """Determine if SL or TP triggered inside this bar. Stop wins ties."""
    if side is Side.LONG:
        sl_hit = bar_low <= stop
        tp_hit = bar_high >= target
        if sl_hit and tp_hit:
            return stop, "stop"
        if sl_hit:
            return stop, "stop"
        if tp_hit:
            return target, "take_profit"
        return None
    else:
        sl_hit = bar_high >= stop
        tp_hit = bar_low <= target
        if sl_hit and tp_hit:
            return stop, "stop"
        if sl_hit:
            return stop, "stop"
        if tp_hit:
            return target, "take_profit"
        return None


def backtest_symbol(
    symbol: str,
    df: pd.DataFrame,
    signals: Sequence[Signal],
    params: BacktestParams,
) -> BacktestResult:
    """Run a deterministic backtest for one symbol."""
    res = BacktestResult()
    if len(df) < 2 or not signals:
        return res

    open_ = df["open"].to_numpy(dtype=np.float64)
    high = df["high"].to_numpy(dtype=np.float64)
    low = df["low"].to_numpy(dtype=np.float64)
    close = df["close"].to_numpy(dtype=np.float64)
    times = df.index.to_numpy()

    atr_series = basic.atr(df["high"], df["low"], df["close"], params.atr_length)
    atr_arr = atr_series.bfill().to_numpy(dtype=np.float64)

    occupied_until = -1
    fee = params.fee_rate
    slip = params.slip_rate

    for sig in signals:
        entry_bar = sig.bar_idx + 1
        if entry_bar >= len(df) or entry_bar <= occupied_until:
            continue
        side = sig.side
        raw_entry = open_[entry_bar]
        entry_price = raw_entry * (1 + slip) if side is Side.LONG else raw_entry * (1 - slip)
        qty = params.notional / entry_price

        if params.stop_mode == "atr":
            atr_at_entry = float(atr_arr[entry_bar])
            sl_dist = params.atr_sl_mult * atr_at_entry
            tp_dist = params.atr_tp_mult * atr_at_entry
            sl_pct_eff = sl_dist / entry_price
        else:
            sl_dist = entry_price * params.stop_loss_pct
            tp_dist = entry_price * params.take_profit_pct
            sl_pct_eff = params.stop_loss_pct

        stop = entry_price - sl_dist if side is Side.LONG else entry_price + sl_dist
        target = entry_price + tp_dist if side is Side.LONG else entry_price - tp_dist
        trigger = entry_price * (1 + params.trailing_trigger_pct) if side is Side.LONG else entry_price * (1 - params.trailing_trigger_pct)
        trailing_armed = False
        trail_dist = params.trailing_distance_pct

        exit_reason: str | None = None
        exit_price = None
        exit_bar = None
        first_check_bar = entry_bar + (1 if params.skip_entry_bar_stop else 0)
        for j in range(first_check_bar, len(df)):
            ev = _exit_price_inside_bar(high[j], low[j], open_[j], close[j], stop, target, side)
            if ev is not None:
                exit_price, exit_reason = ev
                exit_bar = j
                break
            # update trailing stop based on close
            if side is Side.LONG:
                if not trailing_armed and high[j] >= trigger:
                    trailing_armed = True
                if trailing_armed:
                    new_stop = high[j] * (1 - trail_dist)
                    if new_stop > stop:
                        stop = new_stop
            else:
                if not trailing_armed and low[j] <= trigger:
                    trailing_armed = True
                if trailing_armed:
                    new_stop = low[j] * (1 + trail_dist)
                    if new_stop < stop:
                        stop = new_stop

        if exit_price is None:
            # close at last available bar
            exit_bar = len(df) - 1
            exit_price = close[exit_bar]
            exit_reason = "session_end"

        # apply slippage on exit
        if side is Side.LONG:
            exit_price *= (1 - slip)
            pnl_quote = qty * (exit_price - entry_price)
        else:
            exit_price *= (1 + slip)
            pnl_quote = qty * (entry_price - exit_price)
        # fees on both legs (taker)
        pnl_quote -= qty * entry_price * fee
        pnl_quote -= qty * exit_price * fee

        pnl_pct = pnl_quote / params.margin_usdt
        _ = sl_pct_eff  # informational; could be exposed later
        res.trades.append(
            Trade(
                symbol=symbol,
                side=side.value,
                entry_idx=entry_bar,
                exit_idx=exit_bar,
                entry_time=pd.Timestamp(times[entry_bar]),
                exit_time=pd.Timestamp(times[exit_bar]),
                entry_price=float(entry_price),
                exit_price=float(exit_price),
                qty=float(qty),
                pnl_quote=float(pnl_quote),
                pnl_pct=float(pnl_pct),
                exit_reason=exit_reason or "unknown",
                bars_held=exit_bar - entry_bar,
            )
        )
        occupied_until = exit_bar

    return res


def aggregate_kpis(results: Iterable[BacktestResult]) -> dict:
    all_trades: list[Trade] = []
    for r in results:
        all_trades.extend(r.trades)
    combined = BacktestResult(trades=all_trades)
    return combined.kpis()
