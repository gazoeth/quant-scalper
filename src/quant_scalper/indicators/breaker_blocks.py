"""Breaker Blocks detection (signal-focused subset of LuxAlgo's indicator).

The full LuxAlgo Pine indicator paints boxes/labels/PD arrays/TP lines on
chart. For a trading bot we only need the underlying *signals*:

* Detect swing highs/lows on the OHLC data (``len`` bars on each side).
* Track Break of Structure (BoS) and Change of Character (CHoCH).
* When a swing extreme is broken in the opposite direction (CHoCH), the
  *origin candle* of the move that created that swing becomes a breaker
  block.
* A bullish breaker (``+BB``) is a previously-broken swing low that price
  returns to from above — it should act as support.
* A bearish breaker (``-BB``) is a previously-broken swing high that price
  returns to from below — it should act as resistance.

We expose two booleans per bar:

* ``bull_breaker_signal`` — price is currently inside a fresh +BB zone
  AND the bar shows a bullish reaction (close > open and close above the
  block midline).
* ``bear_breaker_signal`` — symmetric.

This is enough for the strategy aggregator to use Breaker Blocks as one
multi-confirmation factor.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class BreakerBlock:
    direction: int  # +1 bullish, -1 bearish
    top: float
    bottom: float
    formed_idx: int
    broken: bool = False  # True once price decisively closes through it again


def _pivot_indices(values: np.ndarray, length: int, is_high: bool) -> list[int]:
    """Return indices that are a pivot high (max) or pivot low (min) within
    ``±length`` bars."""
    out: list[int] = []
    n = len(values)
    for i in range(length, n - length):
        window = values[i - length : i + length + 1]
        center = values[i]
        if is_high and center == window.max() and (window.argmax() == length) or (not is_high) and center == window.min() and (window.argmin() == length):
            out.append(i)
    return out


def detect_breaker_blocks(
    df: pd.DataFrame,
    *,
    length: int = 5,
    body_only: bool = False,
    max_bars: int = 2000,
) -> tuple[pd.Series, pd.Series, list[BreakerBlock]]:
    """Compute per-bar bullish / bearish breaker block signals.

    Parameters
    ----------
    df: DataFrame with ``open``, ``high``, ``low``, ``close`` (positional index).
    length: pivot lookback (LuxAlgo default = 5).
    body_only: define block bounds by candle body instead of full wick.
    max_bars: how many recent bars to scan (cap for performance).

    Returns
    -------
    (bull_signal, bear_signal, blocks)
        ``bull_signal`` / ``bear_signal`` are boolean Series aligned with
        ``df.index``. ``blocks`` is the chronological list of detected
        breaker blocks for inspection / reporting.
    """
    required = {"open", "high", "low", "close"}
    if not required.issubset(df.columns):
        raise ValueError(f"DataFrame must have {required}")

    n = len(df)
    bull = np.zeros(n, dtype=bool)
    bear = np.zeros(n, dtype=bool)
    blocks: list[BreakerBlock] = []

    if n < 2 * length + 3:
        return pd.Series(bull, index=df.index), pd.Series(bear, index=df.index), blocks

    high = df["high"].to_numpy(dtype=np.float64)
    low = df["low"].to_numpy(dtype=np.float64)
    open_ = df["open"].to_numpy(dtype=np.float64)
    close = df["close"].to_numpy(dtype=np.float64)

    pivot_highs = _pivot_indices(high, length, is_high=True)
    pivot_lows = _pivot_indices(low, length, is_high=False)

    # Walk the timeline detecting CHoCH points
    # ─────────────────────────────────────────────────
    # bullish setup:  pivot high H1 → broken downward by close < pivot low L1
    #                 → eventually reclaimed: close > H1 ⇒ origin of the
    #                 last leg up becomes the +BB zone.
    # bearish: symmetric.
    pivots = sorted(
        [(idx, high[idx], 1) for idx in pivot_highs]
        + [(idx, low[idx], -1) for idx in pivot_lows]
    )

    last_swing_high: tuple[int, float] | None = None
    last_swing_low: tuple[int, float] | None = None

    # iterate bar-by-bar so we can react to swings as they "confirm"
    for i in range(n):
        # register newly-confirmed pivots whose center index is i - length
        center = i - length
        if center >= 0:
            if center in pivot_highs:
                last_swing_high = (center, float(high[center]))
            if center in pivot_lows:
                last_swing_low = (center, float(low[center]))

        # check for breaks of structure -> form breaker blocks
        bar_high, bar_low, bar_open, bar_close = high[i], low[i], open_[i], close[i]

        # bullish breaker formation: prior swing high broken upward
        if last_swing_high is not None and bar_close > last_swing_high[1]:
            # find the most recent swing low BEFORE that swing high — the
            # candle around it is the bullish breaker origin
            origin_idx = None
            for lo_idx, _val, _ in [(p[0], p[1], p[2]) for p in pivots if p[2] == -1]:
                if lo_idx < last_swing_high[0]:
                    origin_idx = lo_idx
            if origin_idx is not None:
                top = float(max(open_[origin_idx], close[origin_idx])) if body_only else float(high[origin_idx])
                bot = float(min(open_[origin_idx], close[origin_idx])) if body_only else float(low[origin_idx])
                if not blocks or blocks[-1].formed_idx != i or blocks[-1].direction != 1:
                    blocks.append(
                        BreakerBlock(direction=1, top=top, bottom=bot, formed_idx=i)
                    )
            last_swing_high = None  # consumed

        if last_swing_low is not None and bar_close < last_swing_low[1]:
            origin_idx = None
            for hi_idx, _val, _ in [(p[0], p[1], p[2]) for p in pivots if p[2] == 1]:
                if hi_idx < last_swing_low[0]:
                    origin_idx = hi_idx
            if origin_idx is not None:
                top = float(max(open_[origin_idx], close[origin_idx])) if body_only else float(high[origin_idx])
                bot = float(min(open_[origin_idx], close[origin_idx])) if body_only else float(low[origin_idx])
                if not blocks or blocks[-1].formed_idx != i or blocks[-1].direction != -1:
                    blocks.append(
                        BreakerBlock(direction=-1, top=top, bottom=bot, formed_idx=i)
                    )
            last_swing_low = None

        # generate signals when current bar revisits a still-valid block
        for blk in blocks[-20:]:  # only scan recent blocks
            if blk.broken:
                continue
            if i - blk.formed_idx > max_bars:
                continue
            if blk.direction == 1:
                # bullish: bar dipped into zone and closed back above midline
                mid = (blk.top + blk.bottom) / 2.0
                touched = bar_low <= blk.top and bar_low >= blk.bottom * 0.98
                if touched and bar_close > mid and bar_close > bar_open:
                    bull[i] = True
                # invalidate if closed below the block
                if bar_close < blk.bottom:
                    blk.broken = True
            else:
                mid = (blk.top + blk.bottom) / 2.0
                touched = bar_high >= blk.bottom and bar_high <= blk.top * 1.02
                if touched and bar_close < mid and bar_close < bar_open:
                    bear[i] = True
                if bar_close > blk.top:
                    blk.broken = True

    return pd.Series(bull, index=df.index, name="bull_breaker"), pd.Series(
        bear, index=df.index, name="bear_breaker"
    ), blocks
