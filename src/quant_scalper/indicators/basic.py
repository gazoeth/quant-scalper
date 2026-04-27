"""Vectorised technical indicators (EMA, RSI, Bollinger, ATR, MACD)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


def ema(series: pd.Series, length: int) -> pd.Series:
    """Exponential moving average matching TradingView's ``ta.ema``."""
    return series.ewm(span=length, adjust=False, min_periods=length).mean()


def sma(series: pd.Series, length: int) -> pd.Series:
    return series.rolling(length, min_periods=length).mean()


def rsi(series: pd.Series, length: int = 14) -> pd.Series:
    """Wilder-smoothed RSI (matches TradingView's ``ta.rsi``)."""
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    avg_loss = loss.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    return out.fillna(50.0)


@dataclass(frozen=True)
class Bollinger:
    middle: pd.Series
    upper: pd.Series
    lower: pd.Series
    width: pd.Series
    percent_b: pd.Series


def bollinger_bands(series: pd.Series, length: int = 20, std_mult: float = 2.0) -> Bollinger:
    mid = sma(series, length)
    std = series.rolling(length, min_periods=length).std(ddof=0)
    upper = mid + std_mult * std
    lower = mid - std_mult * std
    width = (upper - lower) / mid.replace(0.0, np.nan)
    pct_b = (series - lower) / (upper - lower).replace(0.0, np.nan)
    return Bollinger(middle=mid, upper=upper, lower=lower, width=width, percent_b=pct_b)


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr


def atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.Series:
    """Wilder ATR (matches TradingView's ``ta.atr``)."""
    tr = true_range(high, low, close)
    return tr.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()


@dataclass(frozen=True)
class MACD:
    line: pd.Series
    signal: pd.Series
    histogram: pd.Series


def macd(
    series: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> MACD:
    fast_ema = ema(series, fast)
    slow_ema = ema(series, slow)
    line = fast_ema - slow_ema
    sig = ema(line, signal)
    return MACD(line=line, signal=sig, histogram=line - sig)
