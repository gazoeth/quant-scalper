"""Command-line entry points: ``quant-scalper backtest`` and ``quant-scalper live``."""

from __future__ import annotations

import json
from pathlib import Path

import click

from .config import RunMode, get_settings
from .utils.logging import configure


@click.group()
@click.option("--log-level", default=None, help="Override log level")
def main(log_level: str | None) -> None:
    s = get_settings()
    configure(level=log_level or s.log_level, logfile=Path("data/logs/quant_scalper.log"))


@main.command("backtest")
@click.option("--symbols", default="", help="Comma-separated symbols. Empty = top USDT perps.")
@click.option("--top", default=30, type=int, help="If --symbols empty, scan top N symbols.")
@click.option("--months", default=12, type=int, help="History window length in months.")
@click.option("--report", default="data/reports/backtest.html", help="HTML report output path.")
@click.option("--csv", default="data/reports/trades.csv", help="CSV trade dump.")
@click.option("--params-json", default=None, help="Optional JSON file with strategy + risk params.")
def backtest_cmd(symbols: str, top: int, months: int, report: str, csv: str, params_json: str | None) -> None:
    """Run a multi-symbol backtest and write an HTML / CSV report."""
    from .strategy.runner import run_multi_symbol_backtest

    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    overrides = {}
    if params_json:
        overrides = json.loads(Path(params_json).read_text())

    summary = run_multi_symbol_backtest(
        symbols=syms,
        top_n=top,
        months=months,
        report_path=Path(report),
        csv_path=Path(csv),
        overrides=overrides,
    )
    click.echo(json.dumps(summary["kpis"], indent=2, default=str))


@main.command("live")
@click.option("--dry-run/--real", default=True, help="If --real, place orders. Default is dry-run.")
def live_cmd(dry_run: bool) -> None:
    """Run the live trader. Defaults to dry-run for safety."""
    from .live.runner import run_live

    s = get_settings()
    if not dry_run and s.mode is RunMode.LIVE:
        click.confirm(
            "You are about to trade with REAL FUNDS on Binance live. Continue?",
            abort=True,
        )
    run_live(dry_run=dry_run)


@main.command("fetch")
@click.argument("symbol")
@click.option("--timeframe", default="15m")
@click.option("--months", default=6, type=int)
def fetch_cmd(symbol: str, timeframe: str, months: int) -> None:
    """Pre-populate the historical-data cache for one symbol."""
    from .exchange.data import HistoryRequest, fetch_history

    df = fetch_history(HistoryRequest(symbol=symbol, timeframe=timeframe, months=months))
    click.echo(f"{symbol} {timeframe} {months}mo: {len(df)} bars")
    click.echo(df.tail(3))


if __name__ == "__main__":
    main()
