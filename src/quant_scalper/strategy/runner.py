"""Orchestrates: fetch history → run signals → run per-symbol backtest →
aggregate KPIs → write HTML/CSV report."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import pandas as pd

from ..config import get_settings
from ..exchange.data import HistoryRequest, fetch_history, list_top_usdt_perp
from ..utils.logging import logger
from .backtest import BacktestParams, BacktestResult, aggregate_kpis, backtest_symbol
from .signal import StrategyParams, generate_signals


def _resolve_symbols(symbols: list[str], top_n: int) -> list[str]:
    if symbols:
        return symbols
    return list_top_usdt_perp(limit=top_n)


def _build_params(overrides: dict) -> tuple[StrategyParams, BacktestParams]:
    sp_kwargs = overrides.get("strategy", {})
    bp_kwargs = overrides.get("backtest", {})
    return StrategyParams(**sp_kwargs), BacktestParams(**bp_kwargs)


def _per_symbol_backtest(
    symbol: str,
    months: int,
    timeframe: str,
    mtf_timeframe: str,
    sp: StrategyParams,
    bp_template: BacktestParams,
) -> tuple[str, BacktestResult, int]:
    try:
        df = fetch_history(HistoryRequest(symbol, timeframe, months=months))
        mtf = fetch_history(HistoryRequest(symbol, mtf_timeframe, months=months))
    except Exception as e:
        logger.warning(f"{symbol}: fetch failed: {e}")
        return symbol, BacktestResult(), 0
    if df.empty:
        return symbol, BacktestResult(), 0

    sigs = generate_signals(df, mtf_df=mtf, params=sp)
    settings = get_settings()
    bp = BacktestParams(
        stop_loss_pct=bp_template.stop_loss_pct,
        take_profit_pct=bp_template.take_profit_pct,
        trailing_trigger_pct=bp_template.trailing_trigger_pct,
        trailing_distance_pct=bp_template.trailing_distance_pct,
        fee_bps=bp_template.fee_bps,
        slippage_bps=bp_template.slippage_bps,
        margin_usdt=settings.margin_for(symbol),
        leverage=settings.leverage_for(symbol),
    )
    res = backtest_symbol(symbol, df, sigs, bp)
    return symbol, res, len(sigs)


def _render_html(symbol_kpis: list[dict], overall: dict, params_dump: dict) -> str:
    rows = "".join(
        f"<tr><td>{r['symbol']}</td><td>{r['trades']}</td>"
        f"<td>{r['win_rate']:.2%}</td><td>{r['expectancy_quote']:+.2f}</td>"
        f"<td>{r['total_pnl_quote']:+.2f}</td><td>{r['profit_factor']:.2f}</td>"
        f"<td>{r['avg_bars']:.1f}</td></tr>"
        for r in symbol_kpis
    )
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>quant-scalper backtest</title>
<style>
body{{font-family:-apple-system,Segoe UI,sans-serif;max-width:1100px;margin:24px auto;padding:0 16px;}}
h1{{margin-bottom:4px}}
table{{border-collapse:collapse;width:100%;font-size:14px;margin-top:12px}}
th,td{{border:1px solid #ddd;padding:6px 10px;text-align:right}}
th{{background:#f5f5f5}} td:first-child,th:first-child{{text-align:left}}
.kpi{{display:inline-block;background:#f5f5f5;border-radius:6px;padding:8px 14px;margin:4px 6px}}
.kpi b{{font-size:18px}}
pre{{background:#f8f8f8;padding:12px;border-radius:6px;font-size:12px;overflow:auto}}
</style></head><body>
<h1>quant-scalper backtest</h1>
<div>
<span class="kpi">Trades <b>{overall['trades']}</b></span>
<span class="kpi">Win-rate <b>{overall['win_rate']:.2%}</b></span>
<span class="kpi">Expectancy <b>{overall['expectancy_quote']:+.2f}U</b></span>
<span class="kpi">Profit factor <b>{overall['profit_factor']:.2f}</b></span>
<span class="kpi">Total PnL <b>{overall['total_pnl_quote']:+.2f}U</b></span>
<span class="kpi">Avg bars <b>{overall['avg_bars']:.1f}</b></span>
</div>
<h2>Per-symbol KPIs</h2>
<table><tr><th>Symbol</th><th>Trades</th><th>Win-rate</th><th>Expectancy</th><th>Total PnL</th><th>PF</th><th>Avg bars</th></tr>
{rows}
</table>
<h2>Params</h2>
<pre>{params_dump}</pre>
</body></html>"""


def run_multi_symbol_backtest(
    *,
    symbols: list[str],
    top_n: int,
    months: int,
    report_path: Path,
    csv_path: Path,
    overrides: dict,
) -> dict:
    settings = get_settings()
    sp, bp = _build_params(overrides)
    syms = _resolve_symbols(symbols, top_n)
    logger.info(f"Backtesting {len(syms)} symbols × {months} months @ {settings.timeframe}")

    per_symbol: list[dict] = []
    all_results: list[BacktestResult] = []
    all_trades_dfs: list[pd.DataFrame] = []
    for sym in syms:
        sym, res, n_sig = _per_symbol_backtest(
            sym, months, settings.timeframe, settings.mtf_timeframe, sp, bp
        )
        kpis = res.kpis()
        kpis["symbol"] = sym
        kpis["signals"] = n_sig
        per_symbol.append(kpis)
        all_results.append(res)
        if res.trades:
            all_trades_dfs.append(res.to_frame())
        logger.info(
            f"{sym}: signals={n_sig} trades={kpis['trades']} "
            f"wr={kpis['win_rate']:.2%} exp={kpis['expectancy_quote']:+.2f}"
        )

    overall = aggregate_kpis(all_results)

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    if all_trades_dfs:
        pd.concat(all_trades_dfs).to_csv(csv_path, index=False)

    report_path.parent.mkdir(parents=True, exist_ok=True)
    params_dump = {
        "strategy": asdict(sp),
        "backtest_template": asdict(bp),
        "settings": {
            "timeframe": settings.timeframe,
            "mtf_timeframe": settings.mtf_timeframe,
            "major_symbols": settings.major_symbols,
            "major_leverage": settings.major_leverage,
            "alt_leverage": settings.alt_leverage,
        },
    }
    report_path.write_text(_render_html(per_symbol, overall, params_dump))
    logger.info(f"Report → {report_path}")
    logger.info(f"Trades CSV → {csv_path}")
    return {"kpis": overall, "per_symbol": per_symbol, "report": str(report_path)}
