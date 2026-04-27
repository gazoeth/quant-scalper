"""Sanity tests for indicators and Chan Lun primitives."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant_scalper.indicators import basic, breaker_blocks, chan_lun


def _synthetic_ohlc(n: int = 400, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    steps = rng.normal(0, 1, n).cumsum() + 100.0
    high = steps + rng.uniform(0.1, 0.6, n)
    low = steps - rng.uniform(0.1, 0.6, n)
    close = steps + rng.normal(0, 0.2, n)
    open_ = np.concatenate([[steps[0]], close[:-1]])
    idx = pd.date_range("2024-01-01", periods=n, freq="15min")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": 1.0},
        index=idx,
    )


def test_ema_matches_pandas_ewm():
    s = pd.Series(np.linspace(1, 100, 100))
    e = basic.ema(s, 10)
    assert e.iloc[9] == pytest.approx(s.ewm(span=10, adjust=False).mean().iloc[9], rel=1e-6)


def test_rsi_bounds():
    df = _synthetic_ohlc()
    r = basic.rsi(df["close"], 14)
    assert r.between(0, 100).all()


def test_bollinger_widths_positive():
    df = _synthetic_ohlc()
    bb = basic.bollinger_bands(df["close"], 20, 2.0)
    valid = bb.upper.notna() & bb.lower.notna()
    assert (bb.upper[valid] >= bb.lower[valid]).all()


def test_macd_zero_when_constant():
    s = pd.Series([42.0] * 80)
    m = basic.macd(s)
    # Once warmed up the histogram should be ~0 for a flat series.
    assert m.histogram.dropna().abs().max() < 1e-6


def test_chan_lun_runs_and_returns_strokes():
    df = _synthetic_ohlc(600).reset_index(drop=True)
    res = chan_lun.analyze(df, min_k=4)
    assert res.fractals, "expected at least one fractal in synthetic data"
    # alternating tops/bottoms
    if len(res.fractals) >= 2:
        for a, b in zip(res.fractals, res.fractals[1:]):
            assert a.is_top != b.is_top
    # strokes are directionally consistent with their endpoints
    for stroke in res.strokes:
        if stroke.is_up:
            assert stroke.e_price > stroke.s_price
        else:
            assert stroke.e_price < stroke.s_price


def test_breaker_block_signals_smoke():
    df = _synthetic_ohlc(800)
    bull, bear, blocks = breaker_blocks.detect_breaker_blocks(df, length=5)
    assert len(bull) == len(df) and len(bear) == len(df)
    # signals are booleans
    assert bull.dtype == bool and bear.dtype == bool
    # blocks contain only +1 / -1 directions
    for b in blocks:
        assert b.direction in (1, -1)


def test_inclusion_does_not_explode_on_short_series():
    # Should handle small inputs gracefully
    df = _synthetic_ohlc(20).reset_index(drop=True)
    res = chan_lun.analyze(df, min_k=4)
    assert isinstance(res, chan_lun.ChanResult)
