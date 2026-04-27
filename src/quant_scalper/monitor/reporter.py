"""Telegram + console PnL reporter (best-effort, no hard dep on Telegram)."""

from __future__ import annotations

import httpx

from ..config import get_settings
from ..utils.logging import logger


class Reporter:
    def __init__(self) -> None:
        s = get_settings()
        self.tg_token = s.telegram_bot_token
        self.tg_chat = s.telegram_chat_id
        self.enabled = bool(self.tg_token and self.tg_chat)

    def _telegram(self, text: str) -> None:
        if not self.enabled:
            return
        url = f"https://api.telegram.org/bot{self.tg_token}/sendMessage"
        try:
            with httpx.Client(timeout=8.0) as client:
                client.post(url, data={"chat_id": self.tg_chat, "text": text, "parse_mode": "Markdown"})
        except Exception as e:
            logger.warning(f"telegram send failed: {e}")

    def opened(self, symbol: str, side: str, qty: float, price: float, leverage: float) -> None:
        msg = f"\U0001f7e2 *Opened* `{symbol}` {side.upper()} qty={qty:.6f} @ {price:.4f} (lev {leverage:.0f}x)"
        logger.info(msg.replace("`", "").replace("*", ""))
        self._telegram(msg)

    def closed(self, symbol: str, side: str, qty: float, price: float, pnl_quote: float, pnl_pct: float, reason: str) -> None:
        emoji = "\U0001f4b0" if pnl_quote > 0 else "\U0001f494"
        msg = (
            f"{emoji} *Closed* `{symbol}` {side.upper()} qty={qty:.6f} @ {price:.4f}\n"
            f"PnL: {pnl_quote:+.2f} USDT ({pnl_pct:+.2%}) — {reason}"
        )
        logger.info(msg.replace("`", "").replace("*", ""))
        self._telegram(msg)

    def heartbeat(self, summary: dict) -> None:
        msg = (
            f"\u23f1 Heartbeat — trades today: {summary.get('n', 0)}, "
            f"wins: {summary.get('wins', 0)}, pnl: {summary.get('pnl', 0):+.2f} USDT"
        )
        logger.info(msg)
        self._telegram(msg)

    def error(self, text: str) -> None:
        msg = f"\u26a0 *Error*: {text}"
        logger.error(text)
        self._telegram(msg)
