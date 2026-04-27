"""End-to-end connectivity check for the Binance USDT-M futures testnet.

Run this *before* enabling the live trader to make sure your machine can:

1. Resolve and reach the proxy (if ``QS_PROXY_URL`` is set).
2. Reach the public testnet endpoint (server time + symbol list).
3. Authenticate with your API keys (fetch balance + position list).
4. Submit and cancel a real limit order on the testnet (uses tiny size,
   placed far from the market price so it cannot fill).

Each step prints PASS / FAIL with an explanation.  The script exits non-zero
on the first failure so it can be wired into a pre-flight ``.bat`` wrapper.
"""

from __future__ import annotations

import os
import socket
import sys
import time
from typing import Any
from urllib.parse import urlparse

# Ensure the connector loads keys from .env in the project root, not just CWD.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

import ccxt  # noqa: E402

from ..config import RunMode, get_settings  # noqa: E402

# Symbols / sizes used for the round-trip order test.  Tiny notional + price
# *very* far from market means the order will sit unfilled until cancellation.
TEST_SYMBOL = "BTC/USDT"
TEST_NOTIONAL_USDT = 5.0  # ~ smallest BTCUSDT notional accepted by Binance
ORDER_FAR_PCT_BELOW = 0.50  # 50 % below market


def _bold(s: str) -> str:
    return f"\x1b[1m{s}\x1b[0m" if sys.stdout.isatty() else s


def _ok(msg: str) -> None:
    print(f"  [PASS] {msg}")


def _fail(msg: str, hint: str | None = None) -> None:
    print(f"  [FAIL] {msg}")
    if hint:
        print(f"         hint: {hint}")
    sys.exit(1)


def _step(title: str) -> None:
    print(f"\n{_bold('==> ' + title)}")


# ──────────────────────────────────────────────────────────────────────


def step_proxy_reachable() -> str | None:
    _step("1. Proxy reachability (QS_PROXY_URL / HTTPS_PROXY)")
    s = get_settings()
    proxy = s.proxy_url or os.environ.get("HTTPS_PROXY") or os.environ.get("ALL_PROXY")
    if not proxy:
        print("  (no proxy configured — connecting directly)")
        return None
    parsed = urlparse(proxy)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (1080 if "socks" in (parsed.scheme or "") else 8080)
    try:
        sock = socket.create_connection((host, int(port)), timeout=4)
        sock.close()
        _ok(f"reached {host}:{port} ({parsed.scheme})")
        return proxy
    except Exception as e:
        _fail(f"could not connect to proxy at {host}:{port}: {e}",
              hint="Make sure your Clash / V2Ray / sing-box client is running and that the port matches.")


def step_public_endpoint(proxy: str | None) -> ccxt.binanceusdm:
    _step("2. Public testnet endpoint (server time + market list)")
    params: dict[str, Any] = {
        "enableRateLimit": True,
        "timeout": 15000,
        "options": {"defaultType": "future"},
    }
    if proxy:
        params["proxies"] = {"http": proxy, "https": proxy}
    ex = ccxt.binanceusdm(params)
    ex.set_sandbox_mode(True)
    try:
        t = ex.fetch_time()
        skew_ms = abs(int(time.time() * 1000) - t)
        _ok(f"server time = {t}  (clock skew {skew_ms} ms)")
        if skew_ms > 5000:
            print(f"  [WARN] clock skew {skew_ms} ms exceeds 5 s; consider running w32tm /resync.")
        markets = ex.load_markets()
        _ok(f"loaded {len(markets)} testnet markets")
    except Exception as e:
        _fail(f"could not reach testnet: {e}",
              hint=("If you see HTTP 451: your IP / proxy egress is geo-blocked; "
                    "try a different proxy node (HK / JP / SG residential preferred)."))
    return ex


def step_auth(ex: ccxt.binanceusdm) -> None:
    _step("3. Authenticated calls (balance + open positions)")
    s = get_settings()
    if not s.binance_api_key or not s.binance_api_secret:
        _fail("BINANCE_API_KEY / BINANCE_API_SECRET not set in .env",
              hint=("Create a *testnet* key at https://testnet.binancefuture.com → "
                    "right-side panel 'API Key'. Paste both into .env."))
    ex.apiKey = s.binance_api_key
    ex.secret = s.binance_api_secret
    try:
        bal = ex.fetch_balance()
        usdt_total = float(bal.get("USDT", {}).get("total") or bal.get("total", {}).get("USDT") or 0)
        _ok(f"USDT balance: {usdt_total:.2f}")
    except Exception as e:
        _fail(f"fetch_balance failed: {e}",
              hint=("Wrong key / secret, or wrong account type (must be a Binance "
                    "Futures TESTNET key, not the spot testnet, not mainnet)."))
    try:
        positions = ex.fetch_positions([TEST_SYMBOL])
        _ok(f"fetch_positions returned {len(positions)} entries")
    except Exception as e:
        _fail(f"fetch_positions failed: {e}")


def step_round_trip_order(ex: ccxt.binanceusdm) -> None:
    _step("4. Round-trip limit order (place + cancel)")
    try:
        ticker = ex.fetch_ticker(TEST_SYMBOL)
        last = float(ticker["last"])
    except Exception as e:
        _fail(f"fetch_ticker {TEST_SYMBOL} failed: {e}")
    far_price = round(last * (1 - ORDER_FAR_PCT_BELOW), 1)
    qty = round(TEST_NOTIONAL_USDT / far_price, 6)
    print(f"  market last = {last}, placing BUY limit at {far_price} (≈ -{int(ORDER_FAR_PCT_BELOW*100)}%) qty={qty}")
    try:
        ex.set_leverage(1, TEST_SYMBOL)
    except Exception as e:
        print(f"  [WARN] set_leverage(1) skipped: {e}")
    try:
        order = ex.create_order(TEST_SYMBOL, "limit", "buy", qty, far_price,
                                {"timeInForce": "GTC", "reduceOnly": False})
        _ok(f"order accepted, id = {order['id']}, status = {order.get('status')}")
    except Exception as e:
        _fail(f"create_order failed: {e}",
              hint=("Most common: API key lacks Futures permission, or the testnet "
                    "account has 0 USDT.  Use the 'Faucet' on the testnet page to fund it."))
    try:
        ex.cancel_order(order["id"], TEST_SYMBOL)
        _ok("order cancelled cleanly")
    except Exception as e:
        _fail(f"cancel_order failed: {e}",
              hint="Try again, then cancel manually on the testnet UI if needed.")


def main() -> None:
    print(_bold("quant-scalper connectivity check") + "  (Binance USDT-M testnet)")
    s = get_settings()
    print(f"  mode      = {s.mode.value}")
    print(f"  api key   = {'<set>' if s.binance_api_key else '<missing>'}")
    print(f"  proxy_url = {s.proxy_url or '(none)'}")
    if s.mode is RunMode.LIVE:
        _fail("QS_MODE is set to 'live'. Refusing to run round-trip order check on mainnet.",
              hint="Set QS_MODE=testnet in .env before running this script.")
    proxy = step_proxy_reachable()
    ex = step_public_endpoint(proxy)
    step_auth(ex)
    step_round_trip_order(ex)
    print()
    print(_bold("ALL CHECKS PASSED ✓"))
    print("You can now run scripts\\run_live_real.bat against testnet with confidence.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)
