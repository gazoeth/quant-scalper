"""Binance USDT-M futures historical klines.

Live ccxt access to ``fapi.binance.com`` is geo-restricted from many cloud
VMs (HTTP 451). For backtest data we therefore use Binance's public klines
archive at ``https://data.binance.vision``, which serves monthly + daily
ZIP files of CSV klines. The schema matches the ``futures/klines`` REST
endpoint exactly, so backtests are on identical data to what the live bot
would see.

For *live* trading we still wire ccxt.binanceusdm — that path is only used
by the live runner, which the user is expected to run from a Binance-
allowed network.
"""

from __future__ import annotations

import io
import time
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import httpx
import pandas as pd

from ..utils.logging import logger

CACHE_DIR = Path("data/cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

BASE = "https://data.binance.vision/data/futures/um"
_KLINES_COLS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "count",
    "taker_buy_volume", "taker_buy_quote_volume", "ignore",
]

_TIMEFRAME_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
    "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000,
    "4h": 14_400_000, "1d": 86_400_000,
}


def _to_archive_symbol(symbol: str) -> str:
    """``BTC/USDT`` or ``BTC/USDT:USDT`` → ``BTCUSDT``."""
    s = symbol.split(":")[0]
    return s.replace("/", "")


def _monthly_url(symbol: str, tf: str, year: int, month: int) -> str:
    s = _to_archive_symbol(symbol)
    return f"{BASE}/monthly/klines/{s}/{tf}/{s}-{tf}-{year:04d}-{month:02d}.zip"


def _daily_url(symbol: str, tf: str, date: pd.Timestamp) -> str:
    s = _to_archive_symbol(symbol)
    return f"{BASE}/daily/klines/{s}/{tf}/{s}-{tf}-{date:%Y-%m-%d}.zip"


def _download_zip(url: str, client: httpx.Client) -> pd.DataFrame | None:
    try:
        r = client.get(url, timeout=30.0)
    except httpx.HTTPError as e:
        logger.warning(f"GET {url}: {e}")
        return None
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        logger.warning(f"GET {url} → {r.status_code}")
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            name = z.namelist()[0]
            with z.open(name) as f:
                df = pd.read_csv(f, header=None, names=_KLINES_COLS)
    except (zipfile.BadZipFile, ValueError) as e:
        logger.warning(f"bad zip from {url}: {e}")
        return None
    # very recent files include a header row — drop it if present
    if df.iloc[0]["open_time"] == "open_time":
        df = df.iloc[1:].reset_index(drop=True)
    df = df.astype({c: "float64" for c in ["open", "high", "low", "close", "volume"]})
    df["open_time"] = pd.to_numeric(df["open_time"]).astype("int64")
    return df


def _cache_path(symbol: str, tf: str, since_ms: int, until_ms: int) -> Path:
    safe = _to_archive_symbol(symbol)
    return CACHE_DIR / f"{safe}_{tf}_{since_ms}_{until_ms}.parquet"


def fetch_ohlcv(
    symbol: str,
    timeframe: str,
    since_ms: int,
    until_ms: int,
) -> pd.DataFrame:
    """Fetch closed candles in ``[since_ms, until_ms)`` from data.binance.vision."""
    if timeframe not in _TIMEFRAME_MS:
        raise ValueError(f"unsupported timeframe {timeframe}")

    cache = _cache_path(symbol, timeframe, since_ms, until_ms)
    if cache.exists():
        return pd.read_parquet(cache)

    start = pd.to_datetime(since_ms, unit="ms")
    end = pd.to_datetime(until_ms, unit="ms")
    frames: list[pd.DataFrame] = []
    with httpx.Client(headers={"User-Agent": "quant-scalper/0.1"}) as client:
        # monthly archives
        cursor = start.replace(day=1)
        last_complete_month = (pd.Timestamp.utcnow().tz_localize(None) - pd.DateOffset(days=1)).replace(day=1)
        while cursor <= end and cursor < last_complete_month:
            df = _download_zip(_monthly_url(symbol, timeframe, cursor.year, cursor.month), client)
            if df is not None and not df.empty:
                frames.append(df)
            cursor = (cursor + pd.DateOffset(months=1))
            time.sleep(0.05)

        # daily archives for the partial current/recent month
        d_cursor = max(cursor, start.normalize())
        today = pd.Timestamp.utcnow().tz_localize(None).normalize()
        while d_cursor < end and d_cursor < today:
            df = _download_zip(_daily_url(symbol, timeframe, d_cursor), client)
            if df is not None and not df.empty:
                frames.append(df)
            d_cursor = d_cursor + pd.Timedelta(days=1)
            time.sleep(0.02)

    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates(subset="open_time").sort_values("open_time").reset_index(drop=True)
    df = df[(df["open_time"] >= since_ms) & (df["open_time"] < until_ms)]
    df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms", utc=True).dt.tz_convert(None)
    out = df.set_index("timestamp")[["open", "high", "low", "close", "volume"]].astype("float64")
    out.to_parquet(cache)
    logger.info(f"{symbol} {timeframe}: {len(out)} bars cached → {cache}")
    return out


@dataclass(frozen=True)
class HistoryRequest:
    symbol: str
    timeframe: str
    months: int = 12

    def since_until(self, end: pd.Timestamp | None = None) -> tuple[int, int]:
        end = end or pd.Timestamp.utcnow().tz_localize(None).floor("h")
        start = end - pd.DateOffset(months=self.months)
        return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def fetch_history(req: HistoryRequest) -> pd.DataFrame:
    since, until = req.since_until()
    return fetch_ohlcv(req.symbol, req.timeframe, since, until)


# ─── Symbol discovery ────────────────────────────────────────────────────
# The futures archive doesn't expose a JSON listing, but we can query the
# (geo-blocked) ccxt endpoint OR use a hard-coded top-N list. We provide a
# default list of the most-liquid USDT-perpetuals which we'll use for
# backtests.
# Default symbol universe — picked from 30-symbol baseline backtest after
# pruning low-WR symbols (TRX/USDT, ATOM/USDT, BCH/USDT, TON/USDT) which
# consistently underperformed the strategy.
DEFAULT_TOP_SYMBOLS = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT",
    "DOGE/USDT", "ADA/USDT", "AVAX/USDT", "LINK/USDT",
    "NEAR/USDT", "DOT/USDT", "MATIC/USDT", "LTC/USDT",
    "OP/USDT", "ARB/USDT", "APT/USDT", "SUI/USDT",
    "FIL/USDT", "ETC/USDT", "ICP/USDT", "INJ/USDT",
    "TIA/USDT", "SEI/USDT", "PEPE/USDT", "WIF/USDT", "FET/USDT",
]


def list_top_usdt_perp(limit: int = 30, exclude: Iterable[str] = ()) -> list[str]:
    excl = set(exclude)
    return [s for s in DEFAULT_TOP_SYMBOLS if s not in excl][:limit]
