"""Chan Lun (缠论) primitives.

Pragmatic Python port of the structural pieces we actually need for trading
signals. The implementation deliberately mirrors the Pine v6 reference
(``CL_AI_v7.6_fixed2.pine``) for the rules that matter at signal time:

* Inclusion handling (包含关系) — collapse engulfing bars in the active
  direction so that fractal detection sees clean swings.
* Fractals (分型) — three-bar pivot tops/bottoms on the *processed* highs/lows.
* Strokes (笔) — alternating fractals separated by ``min_k`` raw bars,
  forming up/down legs.
* Pivots (中枢) — three consecutive overlapping strokes form a Zhongshu
  with ZG / ZD / ZZ levels.
* Divergence (背驰) — comparing MACD histogram area between same-direction
  strokes to flag exhaustion.

The implementation is **bar-confirmed only**: callers should feed the closed
bars they want analysed and treat the last few bars as provisional. This
matches the Pine ``barstate.isconfirmed`` guard.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .basic import macd as macd_indicator

# ─────────────────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class Fractal:
    """A confirmed top/bottom fractal (分型)."""

    idx: int
    price: float
    is_top: bool


@dataclass
class Stroke:
    """A 笔 — directed move between two fractals."""

    s_idx: int
    s_price: float
    e_idx: int
    e_price: float
    is_up: bool
    macd_area: float = 0.0
    divergent: bool = False
    """True if, at the moment this stroke completed, it formed a price-vs-MACD
    divergence with the previous same-direction stroke (i.e., new price extreme
    but smaller MACD area)."""

    @property
    def amplitude(self) -> float:
        return abs(self.e_price - self.s_price)

    @property
    def length(self) -> int:
        return self.e_idx - self.s_idx


@dataclass
class Pivot:
    """A 中枢 — overlap of three consecutive strokes."""

    s_idx: int
    e_idx: int
    zg: float  # upper boundary (min of stroke highs)
    zd: float  # lower boundary (max of stroke lows)
    stroke_count: int

    @property
    def zz(self) -> float:
        return (self.zg + self.zd) / 2.0


@dataclass
class ChanResult:
    fractals: list[Fractal] = field(default_factory=list)
    strokes: list[Stroke] = field(default_factory=list)
    pivots: list[Pivot] = field(default_factory=list)
    last_divergence: str | None = None  # "top" | "bottom" | None

    def latest_pivot(self) -> Pivot | None:
        return self.pivots[-1] if self.pivots else None

    def latest_stroke(self) -> Stroke | None:
        return self.strokes[-1] if self.strokes else None


# ─────────────────────────────────────────────────────────────────────────
# Inclusion handling (包含关系)
# ─────────────────────────────────────────────────────────────────────────


def _process_inclusion(
    high: np.ndarray, low: np.ndarray, init_dir: int = -1
) -> tuple[np.ndarray, np.ndarray]:
    """Collapse engulfing bars per Chan rules; returns processed (h, l).

    This walks the bars left→right tracking direction. When the current
    bar fully contains or is contained by the previous *processed* bar,
    we merge them according to the active direction (max/max in uptrend,
    min/min in downtrend).
    """
    n = len(high)
    out_h = np.empty(n, dtype=np.float64)
    out_l = np.empty(n, dtype=np.float64)
    if n == 0:
        return out_h, out_l
    out_h[0] = high[0]
    out_l[0] = low[0]
    direction = init_dir

    for i in range(1, n):
        ph, pl = out_h[i - 1], out_l[i - 1]
        h, l = high[i], low[i]
        contained = (h <= ph and l >= pl) or (h >= ph and l <= pl)
        if contained:
            if direction >= 0:
                out_h[i] = max(ph, h)
                out_l[i] = max(pl, l)
            else:
                out_h[i] = min(ph, h)
                out_l[i] = min(pl, l)
        else:
            if h > ph and l > pl:
                direction = 1
            elif h < ph and l < pl:
                direction = -1
            out_h[i] = h
            out_l[i] = l
    return out_h, out_l


# ─────────────────────────────────────────────────────────────────────────
# Fractal detection
# ─────────────────────────────────────────────────────────────────────────


def _detect_fractals(h: np.ndarray, l: np.ndarray, lr: int = 2) -> list[Fractal]:
    """Three-bar (or `lr`-bar each side) pivots on processed highs/lows."""
    out: list[Fractal] = []
    n = len(h)
    for i in range(lr, n - lr):
        window_h = h[i - lr : i + lr + 1]
        window_l = l[i - lr : i + lr + 1]
        if h[i] == window_h.max() and (window_h.argmax() == lr):
            out.append(Fractal(idx=i, price=float(h[i]), is_top=True))
        elif l[i] == window_l.min() and (window_l.argmin() == lr):
            out.append(Fractal(idx=i, price=float(l[i]), is_top=False))
    return out


def _dedupe_alternating(fxs: list[Fractal]) -> list[Fractal]:
    """Force alternating top/bottom by keeping the more extreme of consecutive
    same-type fractals."""
    if not fxs:
        return []
    cleaned: list[Fractal] = [fxs[0]]
    for f in fxs[1:]:
        prev = cleaned[-1]
        if f.is_top == prev.is_top:
            if f.is_top and f.price > prev.price or (not f.is_top) and f.price < prev.price:
                cleaned[-1] = f
            # else: ignore weaker duplicate
        else:
            cleaned.append(f)
    return cleaned


# ─────────────────────────────────────────────────────────────────────────
# Strokes
# ─────────────────────────────────────────────────────────────────────────


def _build_strokes(
    fxs: list[Fractal],
    hist: np.ndarray,
    min_k: int = 4,
) -> list[Stroke]:
    """Combine alternating fractals into directional strokes.

    Two fractals must be at least ``min_k`` bars apart (matching the Pine
    setting ``bi_min_k``) and strictly directional (top→bottom = down).
    """
    strokes: list[Stroke] = []
    if len(fxs) < 2:
        return strokes
    cur = fxs[0]
    for nxt in fxs[1:]:
        if nxt.is_top == cur.is_top:
            # same type — should already be collapsed, but be defensive
            if (cur.is_top and nxt.price > cur.price) or (not cur.is_top and nxt.price < cur.price):
                cur = nxt
            continue
        if nxt.idx - cur.idx + 1 < min_k:
            # too short — skip this fractal in favor of waiting for a stronger one
            continue
        is_up = (not cur.is_top) and nxt.is_top
        macd_area = float(np.abs(hist[cur.idx : nxt.idx + 1]).sum())
        s = Stroke(
            s_idx=cur.idx,
            s_price=cur.price,
            e_idx=nxt.idx,
            e_price=nxt.price,
            is_up=is_up,
            macd_area=macd_area,
        )
        # mark divergence if a same-direction predecessor exists with a less-
        # extreme price extreme but a larger MACD area
        prior = next((p for p in reversed(strokes) if p.is_up == s.is_up), None)
        if prior is not None:
            if s.is_up and s.e_price > prior.e_price and s.macd_area < prior.macd_area or (not s.is_up) and s.e_price < prior.e_price and s.macd_area < prior.macd_area:
                s.divergent = True
        strokes.append(s)
        cur = nxt
    return strokes


# ─────────────────────────────────────────────────────────────────────────
# Pivots (中枢)
# ─────────────────────────────────────────────────────────────────────────


def _build_pivots(strokes: list[Stroke]) -> list[Pivot]:
    """Detect Zhongshu — three overlapping consecutive strokes."""
    pivots: list[Pivot] = []
    i = 0
    n = len(strokes)
    while i + 2 < n:
        a, b, c = strokes[i], strokes[i + 1], strokes[i + 2]
        a_hi, a_lo = max(a.s_price, a.e_price), min(a.s_price, a.e_price)
        b_hi, b_lo = max(b.s_price, b.e_price), min(b.s_price, b.e_price)
        c_hi, c_lo = max(c.s_price, c.e_price), min(c.s_price, c.e_price)
        zg = min(a_hi, b_hi, c_hi)
        zd = max(a_lo, b_lo, c_lo)
        if zg > zd:
            count = 3
            j = i + 3
            while j < n:
                d = strokes[j]
                d_hi = max(d.s_price, d.e_price)
                d_lo = min(d.s_price, d.e_price)
                if d_hi >= zd and d_lo <= zg:
                    count += 1
                    j += 1
                else:
                    break
            pivots.append(
                Pivot(
                    s_idx=a.s_idx,
                    e_idx=strokes[i + count - 1].e_idx,
                    zg=float(zg),
                    zd=float(zd),
                    stroke_count=count,
                )
            )
            i += count
        else:
            i += 1
    return pivots


# ─────────────────────────────────────────────────────────────────────────
# Divergence (背驰)
# ─────────────────────────────────────────────────────────────────────────


def _check_divergence(strokes: list[Stroke]) -> str | None:
    """Return ``"top"``/``"bottom"`` if the latest stroke shows MACD-area
    divergence vs the prior same-direction stroke."""
    if len(strokes) < 3:
        return None
    last = strokes[-1]
    # find prior same-direction stroke
    prior = next((s for s in reversed(strokes[:-1]) if s.is_up == last.is_up), None)
    if prior is None:
        return None
    # price made a new extreme but MACD area didn't
    if last.is_up:
        if last.e_price > prior.e_price and last.macd_area < prior.macd_area:
            return "top"
    else:
        if last.e_price < prior.e_price and last.macd_area < prior.macd_area:
            return "bottom"
    return None


# ─────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────


def analyze(
    df: pd.DataFrame,
    *,
    min_k: int = 4,
    fx_lookback: int = 2,
    init_dir: int = -1,
) -> ChanResult:
    """Run the full Chan Lun pipeline on a candles DataFrame.

    ``df`` must have columns ``high``, ``low``, ``close`` and a positional
    integer index aligned with bar number (``df.reset_index(drop=True)``
    is fine).
    """
    if not {"high", "low", "close"}.issubset(df.columns):
        raise ValueError("DataFrame must contain high/low/close columns")
    high = df["high"].to_numpy(dtype=np.float64)
    low = df["low"].to_numpy(dtype=np.float64)
    close = df["close"]

    h_proc, l_proc = _process_inclusion(high, low, init_dir=init_dir)
    fxs = _dedupe_alternating(_detect_fractals(h_proc, l_proc, lr=fx_lookback))
    hist = macd_indicator(close).histogram.fillna(0.0).to_numpy(dtype=np.float64)
    strokes = _build_strokes(fxs, hist, min_k=min_k)
    pivots = _build_pivots(strokes)
    div = _check_divergence(strokes)
    return ChanResult(fractals=fxs, strokes=strokes, pivots=pivots, last_divergence=div)
