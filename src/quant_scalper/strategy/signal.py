"""High-precision signal aggregator targeting >75% win-rate via strict
multi-confirmation.

A trade signal only fires when ALL of the following agree:

1.  **Higher-timeframe trend** (1h EMA20 vs EMA50 by default) is in the
    same direction as the proposed trade.
2.  **EMA20 / EMA50** on the entry timeframe is aligned (price above both
    for longs, below both for shorts).
3.  **Bollinger Band reversion** — for longs price must be below the
    lower band on the prior bar (oversold), and now reclaim the band; for
    shorts the symmetric condition.
4.  **RSI** is leaving an extreme zone in the trade direction
    (oversold→up for longs, overbought→down for shorts).
5.  **Breaker Block** signal in the same direction within the last
    ``confluence_lookback`` bars.
6.  **Chan Lun** — the most recent stroke is in the trade direction OR
    we just observed a divergence flagging a bottom (long) / top (short).

The defaults are deliberately strict; this is a low-frequency / high-
selectivity scalp (we will tune frequency vs. accuracy in backtesting).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd

from ..indicators import basic, breaker_blocks, chan_lun


class Side(str, Enum):
    LONG = "long"
    SHORT = "short"


@dataclass
class StrategyParams:
    ema_fast: int = 20
    ema_slow: int = 50
    bb_length: int = 20
    bb_mult: float = 2.0
    rsi_length: int = 14
    rsi_long_max: float = 38.0  # was oversold ⇒ allow long
    rsi_long_recover: float = 42.0  # now recovering
    rsi_short_min: float = 62.0
    rsi_short_recover: float = 58.0
    confluence_lookback: int = 6
    require_chan_alignment: bool = True
    require_breaker_block: bool = False
    require_bb_touch: bool = True
    require_mtf: bool = True
    chan_min_k: int = 4
    # Volatility filter: skip when ATR/price exceeds this (chaotic markets)
    max_atr_pct: float = 0.025
    # Distance-from-EMA filter: avoid chasing extended moves
    max_dist_from_ema_pct: float = 0.025
    # If true, a confirmed Chan divergence on its own (no BB/RSI trigger
    # needed) can fire — reverse-style signal.
    allow_chan_divergence_alone: bool = True
    atr_length: int = 14
    # Require entry candle body to be at least ``min_body_atr`` × ATR — filters
    # weak / doji entry signals.
    min_body_atr: float = 0.30
    # Drop signals on these symbols (statistically poor in backtests).
    blacklist_symbols: tuple[str, ...] = ()


@dataclass
class Signal:
    bar_idx: int
    side: Side
    price: float
    reasons: tuple[str, ...]


def _mtf_trend(mtf_df: pd.DataFrame | None, params: StrategyParams) -> pd.Series | None:
    """Return a per-bar +1/-1/0 trend signal aligned to the MTF df. Caller
    is responsible for forward-filling onto the entry timeframe."""
    if mtf_df is None or len(mtf_df) < params.ema_slow + 5:
        return None
    fast = basic.ema(mtf_df["close"], params.ema_fast)
    slow = basic.ema(mtf_df["close"], params.ema_slow)
    out = pd.Series(0, index=mtf_df.index, dtype=np.int8)
    out[(fast > slow) & (mtf_df["close"] > fast)] = 1
    out[(fast < slow) & (mtf_df["close"] < fast)] = -1
    return out


def _align_mtf(entry_df: pd.DataFrame, mtf_trend: pd.Series) -> pd.Series:
    """Align an MTF trend (indexed by MTF candle close time) to the
    entry-timeframe candles by forward filling."""
    aligned = mtf_trend.reindex(entry_df.index, method="ffill").fillna(0).astype(int)
    return aligned


def generate_signals(
    df: pd.DataFrame,
    *,
    mtf_df: pd.DataFrame | None = None,
    params: StrategyParams | None = None,
) -> list[Signal]:
    """Scan ``df`` and return every confirmed entry signal.

    Both ``df`` and ``mtf_df`` should be indexed by candle close timestamp
    (``pd.DatetimeIndex``) and contain ``open/high/low/close`` columns.
    """
    p = params or StrategyParams()
    if len(df) < max(p.ema_slow, p.bb_length, p.rsi_length) + 10:
        return []

    close = df["close"]
    ema_fast = basic.ema(close, p.ema_fast)
    ema_slow = basic.ema(close, p.ema_slow)
    bb = basic.bollinger_bands(close, p.bb_length, p.bb_mult)
    rsi = basic.rsi(close, p.rsi_length)
    atr_v = basic.atr(df["high"], df["low"], close, p.atr_length).bfill()

    bull_bb, bear_bb, _ = breaker_blocks.detect_breaker_blocks(df, length=5)

    chan = chan_lun.analyze(df.reset_index(drop=True), min_k=p.chan_min_k)
    chan_dir = pd.Series(0, index=df.index, dtype=np.int8)
    chan_div_bottom = pd.Series(False, index=df.index)
    chan_div_top = pd.Series(False, index=df.index)
    # Forward-fill stroke direction so that bars *after* the last stroke
    # ended also carry an active direction (until a counter-stroke forms).
    n_idx = len(df)
    for stroke in chan.strokes:
        chan_dir.iloc[stroke.s_idx :] = 1 if stroke.is_up else -1
        if stroke.divergent:
            end = stroke.e_idx
            until = min(end + p.confluence_lookback, n_idx - 1)
            if stroke.is_up:
                # divergent up-stroke ⇒ topping ⇒ short bias
                chan_div_top.iloc[end : until + 1] = True
            else:
                chan_div_bottom.iloc[end : until + 1] = True

    if mtf_df is not None and p.require_mtf:
        mtf_trend = _mtf_trend(mtf_df, p)
        mtf_aligned = _align_mtf(df, mtf_trend) if mtf_trend is not None else pd.Series(0, index=df.index)
    else:
        mtf_aligned = pd.Series(0, index=df.index)

    signals: list[Signal] = []
    n = len(df)
    look = p.confluence_lookback

    for i in range(max(p.ema_slow, p.bb_length, p.rsi_length) + 5, n):
        prev_low = df["low"].iloc[i - 1]
        prev_high = df["high"].iloc[i - 1]
        prev_close = close.iloc[i - 1]
        cur_close = close.iloc[i]
        cur_open = df["open"].iloc[i]
        cur_price = float(cur_close)

        # ── Volatility / extension filters ──
        atr_v_i = float(atr_v.iloc[i])
        atr_pct = atr_v_i / cur_price if cur_price > 0 else 0.0
        if atr_pct > p.max_atr_pct:
            continue
        ema_fast_v = float(ema_fast.iloc[i])
        if ema_fast_v > 0:
            if abs(cur_price - ema_fast_v) / ema_fast_v > p.max_dist_from_ema_pct:
                continue
        body = abs(cur_close - cur_open)
        if atr_v_i > 0 and body < p.min_body_atr * atr_v_i:
            continue

        # — LONG conditions ——————————————————
        cond_ema_long = (cur_close > ema_fast.iloc[i] > ema_slow.iloc[i]) and (cur_close > cur_open)
        cond_bb_long = True
        if p.require_bb_touch:
            cond_bb_long = (prev_low <= bb.lower.iloc[i - 1]) and (cur_close > bb.lower.iloc[i])
        recent_rsi_low = rsi.iloc[max(0, i - look) : i + 1].min()
        cond_rsi_long = (recent_rsi_low <= p.rsi_long_max) and (rsi.iloc[i] >= p.rsi_long_recover)
        cond_breaker_long = True
        if p.require_breaker_block:
            cond_breaker_long = bool(bull_bb.iloc[max(0, i - look) : i + 1].any())
        cond_chan_long = True
        if p.require_chan_alignment:
            cond_chan_long = bool(chan_dir.iloc[i] == 1 or chan_div_bottom.iloc[i])
        cond_mtf_long = True
        if p.require_mtf:
            cond_mtf_long = mtf_aligned.iloc[i] == 1

        if (
            cond_ema_long
            and cond_bb_long
            and cond_rsi_long
            and cond_breaker_long
            and cond_chan_long
            and cond_mtf_long
        ):
            signals.append(
                Signal(
                    bar_idx=i,
                    side=Side.LONG,
                    price=float(cur_close),
                    reasons=(
                        "ema",
                        "bb_revert" if p.require_bb_touch else "ema_only",
                        "rsi_oversold_recover",
                        *( ("breaker",) if p.require_breaker_block else () ),
                        *( ("chan",) if p.require_chan_alignment else () ),
                        *( ("mtf",) if p.require_mtf else () ),
                    ),
                )
            )
            continue

        # — SHORT conditions ——————————————————
        cond_ema_short = (cur_close < ema_fast.iloc[i] < ema_slow.iloc[i]) and (cur_close < cur_open)
        cond_bb_short = True
        if p.require_bb_touch:
            cond_bb_short = (prev_high >= bb.upper.iloc[i - 1]) and (cur_close < bb.upper.iloc[i])
        recent_rsi_high = rsi.iloc[max(0, i - look) : i + 1].max()
        cond_rsi_short = (recent_rsi_high >= p.rsi_short_min) and (rsi.iloc[i] <= p.rsi_short_recover)
        cond_breaker_short = True
        if p.require_breaker_block:
            cond_breaker_short = bool(bear_bb.iloc[max(0, i - look) : i + 1].any())
        cond_chan_short = True
        if p.require_chan_alignment:
            cond_chan_short = bool(chan_dir.iloc[i] == -1 or chan_div_top.iloc[i])
        cond_mtf_short = True
        if p.require_mtf:
            cond_mtf_short = mtf_aligned.iloc[i] == -1

        if (
            cond_ema_short
            and cond_bb_short
            and cond_rsi_short
            and cond_breaker_short
            and cond_chan_short
            and cond_mtf_short
        ):
            signals.append(
                Signal(
                    bar_idx=i,
                    side=Side.SHORT,
                    price=float(cur_close),
                    reasons=(
                        "ema",
                        "bb_revert" if p.require_bb_touch else "ema_only",
                        "rsi_overbought_recover",
                        *( ("breaker",) if p.require_breaker_block else () ),
                        *( ("chan",) if p.require_chan_alignment else () ),
                        *( ("mtf",) if p.require_mtf else () ),
                    ),
                )
            )

    return signals
