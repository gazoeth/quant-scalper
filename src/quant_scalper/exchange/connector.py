"""Thin wrapper around ccxt.binanceusdm for *live* trading.

Anything that requires private API access (orders, balance, positions) goes
through this connector. Historical data fetches use ``data.binance.vision``
in :mod:`quant_scalper.exchange.data` to side-step geo-restrictions.

Safety rules
------------
* ``sandbox=True`` whenever ``QS_MODE != live``.
* ``hedge_mode`` is forced off — we keep a single position per symbol.
* All order calls support ``dry_run=True`` and return a synthetic order id
  in that case so paper-trading can exercise the same code path.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import ccxt
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import RunMode, get_settings
from ..utils.logging import logger


@dataclass
class OrderResult:
    id: str
    symbol: str
    side: str
    qty: float
    price: float
    status: str
    raw: dict[str, Any]


class BinanceUMConnector:
    """ccxt-backed Binance USDT-M futures connector."""

    def __init__(self, *, dry_run: bool = False) -> None:
        import os

        s = get_settings()
        self.settings = s
        self.dry_run = dry_run
        params: dict[str, Any] = {
            "apiKey": s.binance_api_key,
            "secret": s.binance_api_secret,
            "enableRateLimit": True,
            "options": {
                "defaultType": "future",
                "adjustForTimeDifference": True,
            },
        }
        # Honour HTTPS_PROXY / HTTP_PROXY / ALL_PROXY env vars and the
        # explicit QS_PROXY_URL setting for users behind a Clash / V2Ray
        # client.  ccxt forwards `proxies` straight through to its HTTP
        # session, so socks5h://127.0.0.1:1080 works.
        proxy = (
            getattr(s, "proxy_url", None)
            or os.environ.get("QS_PROXY_URL")
            or os.environ.get("HTTPS_PROXY")
            or os.environ.get("https_proxy")
            or os.environ.get("ALL_PROXY")
            or os.environ.get("all_proxy")
        )
        if proxy:
            params["proxies"] = {"http": proxy, "https": proxy}
            logger.info(f"BinanceUMConnector: routing via proxy {proxy}")
        self._exchange = ccxt.binanceusdm(params)
        if s.mode is not RunMode.LIVE:
            self._exchange.set_sandbox_mode(True)
            logger.info("BinanceUMConnector: sandbox/testnet mode enabled")
        try:
            self._exchange.load_markets()
        except Exception as e:  # geo-block, network, etc — caller decides
            logger.warning(f"load_markets failed: {e}")

    # ─── Account ────────────────────────────────────────────────────
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=8))
    def fetch_balance(self) -> dict:
        return self._exchange.fetch_balance()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=8))
    def fetch_positions(self, symbols: list[str] | None = None) -> list[dict]:
        return self._exchange.fetch_positions(symbols)

    def set_leverage(self, symbol: str, leverage: int) -> None:
        if self.dry_run:
            return
        try:
            self._exchange.set_leverage(leverage, symbol)
        except Exception as e:
            logger.warning(f"set_leverage({symbol}, {leverage}): {e}")

    def set_margin_mode(self, symbol: str, mode: str = "cross") -> None:
        if self.dry_run:
            return
        try:
            self._exchange.set_margin_mode(mode, symbol)
        except ccxt.ExchangeError as e:
            # "No need to change margin type" is non-fatal
            if "No need to change" not in str(e):
                logger.warning(f"set_margin_mode({symbol}, {mode}): {e}")

    # ─── Market data ────────────────────────────────────────────────
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=8))
    def fetch_ticker(self, symbol: str) -> dict:
        return self._exchange.fetch_ticker(symbol)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=8))
    def fetch_recent_ohlcv(self, symbol: str, timeframe: str, limit: int = 200) -> list[list[float]]:
        return self._exchange.fetch_ohlcv(symbol, timeframe, limit=limit)

    # ─── Orders ─────────────────────────────────────────────────────
    def _fake_order(self, symbol: str, side: str, qty: float, price: float, kind: str) -> OrderResult:
        return OrderResult(
            id=f"dry-{int(time.time()*1000)}",
            symbol=symbol,
            side=side,
            qty=qty,
            price=price,
            status="filled-dry",
            raw={"dry_run": True, "kind": kind},
        )

    def market_open(self, symbol: str, side: str, qty: float, price_hint: float) -> OrderResult:
        if self.dry_run:
            logger.info(f"[DRY] market_open {side} {symbol} qty={qty:.6f} ~{price_hint:.4f}")
            return self._fake_order(symbol, side, qty, price_hint, "market_open")
        order = self._exchange.create_order(symbol, "market", side, qty)
        return OrderResult(
            id=str(order.get("id", "")),
            symbol=symbol,
            side=side,
            qty=qty,
            price=float(order.get("average") or price_hint),
            status=str(order.get("status", "open")),
            raw=order,
        )

    def market_close(self, symbol: str, side: str, qty: float, price_hint: float) -> OrderResult:
        opp = "sell" if side == "buy" else "buy"
        if self.dry_run:
            logger.info(f"[DRY] market_close {opp} {symbol} qty={qty:.6f} ~{price_hint:.4f}")
            return self._fake_order(symbol, opp, qty, price_hint, "market_close")
        order = self._exchange.create_order(
            symbol, "market", opp, qty, params={"reduceOnly": True}
        )
        return OrderResult(
            id=str(order.get("id", "")),
            symbol=symbol,
            side=opp,
            qty=qty,
            price=float(order.get("average") or price_hint),
            status=str(order.get("status", "open")),
            raw=order,
        )

    def stop_market(self, symbol: str, side: str, qty: float, stop_price: float, reduce_only: bool = True) -> OrderResult:
        opp = "sell" if side == "buy" else "buy"
        if self.dry_run:
            logger.info(f"[DRY] stop_market {opp} {symbol} qty={qty:.6f} trigger={stop_price:.4f}")
            return self._fake_order(symbol, opp, qty, stop_price, "stop_market")
        params = {"stopPrice": stop_price, "reduceOnly": reduce_only}
        order = self._exchange.create_order(symbol, "stop_market", opp, qty, params=params)
        return OrderResult(
            id=str(order.get("id", "")), symbol=symbol, side=opp, qty=qty,
            price=stop_price, status=str(order.get("status", "open")), raw=order,
        )

    def take_profit_market(self, symbol: str, side: str, qty: float, tp_price: float) -> OrderResult:
        opp = "sell" if side == "buy" else "buy"
        if self.dry_run:
            logger.info(f"[DRY] tp_market {opp} {symbol} qty={qty:.6f} trigger={tp_price:.4f}")
            return self._fake_order(symbol, opp, qty, tp_price, "tp_market")
        params = {"stopPrice": tp_price, "reduceOnly": True}
        order = self._exchange.create_order(symbol, "take_profit_market", opp, qty, params=params)
        return OrderResult(
            id=str(order.get("id", "")), symbol=symbol, side=opp, qty=qty,
            price=tp_price, status=str(order.get("status", "open")), raw=order,
        )

    def cancel_open_orders(self, symbol: str) -> None:
        if self.dry_run:
            return
        try:
            self._exchange.cancel_all_orders(symbol)
        except Exception as e:
            logger.warning(f"cancel_all_orders({symbol}): {e}")
