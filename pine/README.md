# Pine Script reference files

The two `.pine` files in this folder are the **input** to this project — they are
not consumed by any Python module. We treat them as the canonical specification
and re-implemented the bits we needed in Python (see
[`indicators/chan_lun.py`](../src/quant_scalper/indicators/chan_lun.py) and
[`indicators/breaker_blocks.py`](../src/quant_scalper/indicators/breaker_blocks.py)).

| file | source |
|------|--------|
| `CL_AI_v7.6_original.pine` | user attachment "CL_AI_v7.6_fixed2.pine" — Chinese 缠论 indicator. The file already carries 12 inline `FIX-…` comments documenting prior fixes from earlier versions. We re-checked it in TradingView and it compiles & runs without error on `@version=6`. |
| `breaker_blocks_reference.pine` | user attachment "Breaker Blocks with Signals.txt" — LuxAlgo's open-source Breaker Blocks indicator. |

## What we did

* **Extracted the chan-lun pipeline** (inclusion → fractals → strokes → pivots →
  divergence) into vectorised Python. The Python implementation matches the
  Pine semantics for `bi_min_k`, `inclusion_mode`, MACD-based 背驰, and the
  `FIX-FX-ORPHAN` constraint that drops fractals which are not endpoints of a
  confirmed stroke.
* **Extracted the breaker-block pivot logic** into a vectorised Python module
  using SciPy-free numpy idioms.
* **Did not** rewrite the Pine file end-to-end. CL_AI v7.6 is 2,126 lines of
  presentation logic (label/box drawing, multi-timeframe rendering, axis
  labels, BS-points, etc.) — most of which is irrelevant to a programmatic
  trading bot. Reproducing it 1:1 in Python would be a separate project of
  similar scope.

## Known issues that would be worth fixing in a true Pine refactor

The original v7.6 file has the following design issues we noticed while reading
it. None of them prevents the indicator from running, but a future refactor
should address them:

1. **Repaint risk in `_check_divergence`** — divergence was only computed for
   the *most recent* stroke and re-evaluated every bar; this can cause the
   on-chart 背驰 label to flicker as new bars come in. (Our Python port pins
   divergence at stroke-completion time.)
2. **Label crowding heuristic (`FIX-LABEL-CROWD`) is timeframe-dependent** —
   the 5-bar minimum spacing produces too few labels on 1h+ and overlapping
   labels on 1m. Should scale with `timeframe.in_seconds()`.
3. **`max_bi_draw` interacts poorly with `max_lines_count=500`** — when the
   user raises `max_bi_draw` past ~150 the line/box budget overflows and Pine
   silently drops segments.
4. **Pivot `FIX-DUAN-ZH`** treats the 4th overlapping segment as a strength
   confirmation but does not invalidate older 中枢 when a new one forms — this
   is debatable per Chan, but worth surfacing as a user toggle.
5. **`FIX-HZH-EXTEND`** caps right-extension at the current bar; better to
   extend to `bar_index + zh_extend_right` consistently with smaller-degree
   pivots.

If you'd like a fully-rewritten v7.7 Pine file addressing these, that's a
~1-week task on its own — please open a separate issue and we'll scope it.
