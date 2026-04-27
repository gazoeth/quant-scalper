"""SQLite trade journal. Synchronous, thread-safe per-thread connection."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from ..utils.logging import logger

_LOCK = threading.Lock()
_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    qty REAL NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL,
    entry_time TEXT NOT NULL,
    exit_time TEXT,
    leverage REAL NOT NULL,
    margin_usdt REAL NOT NULL,
    stop_price REAL,
    tp_price REAL,
    pnl_quote REAL,
    pnl_pct REAL,
    exit_reason TEXT,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
CREATE INDEX IF NOT EXISTS idx_trades_open ON trades(exit_time);
"""


@dataclass
class TradeRow:
    id: int
    symbol: str
    side: str
    qty: float
    entry_price: float
    entry_time: str
    leverage: float
    margin_usdt: float
    stop_price: float | None = None
    tp_price: float | None = None
    exit_price: float | None = None
    exit_time: str | None = None
    pnl_quote: float | None = None
    pnl_pct: float | None = None
    exit_reason: str | None = None
    notes: str | None = None


class TradeStore:
    def __init__(self, path: Path = Path("data/quant_scalper.db")) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self._conn() as c:
            c.executescript(_SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        with _LOCK:
            conn = sqlite3.connect(self.path)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
                conn.commit()
            finally:
                conn.close()

    def insert_open(self, *, symbol: str, side: str, qty: float, entry_price: float,
                    entry_time: str, leverage: float, margin_usdt: float,
                    stop_price: float, tp_price: float, notes: str = "") -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO trades(symbol, side, qty, entry_price, entry_time,"
                " leverage, margin_usdt, stop_price, tp_price, notes)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (symbol, side, qty, entry_price, entry_time, leverage, margin_usdt,
                 stop_price, tp_price, notes),
            )
            tid = int(cur.lastrowid)
            logger.debug(f"trade #{tid} opened: {side} {symbol} {qty}@{entry_price}")
            return tid

    def update_close(self, trade_id: int, *, exit_price: float, exit_time: str,
                     pnl_quote: float, pnl_pct: float, exit_reason: str) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE trades SET exit_price=?, exit_time=?, pnl_quote=?, pnl_pct=?, exit_reason=?"
                " WHERE id=?",
                (exit_price, exit_time, pnl_quote, pnl_pct, exit_reason, trade_id),
            )

    def update_stop(self, trade_id: int, stop_price: float) -> None:
        with self._conn() as c:
            c.execute("UPDATE trades SET stop_price=? WHERE id=?", (stop_price, trade_id))

    def list_open(self) -> list[TradeRow]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM trades WHERE exit_time IS NULL").fetchall()
            return [TradeRow(**dict(r)) for r in rows]

    def daily_summary(self, day: str) -> dict:
        """Aggregate trades closed on ``day`` (UTC ISO date YYYY-MM-DD)."""
        with self._conn() as c:
            row = c.execute(
                "SELECT COUNT(*) n, SUM(CASE WHEN pnl_quote>0 THEN 1 ELSE 0 END) wins,"
                " SUM(pnl_quote) pnl"
                " FROM trades WHERE exit_time LIKE ?", (f"{day}%",),
            ).fetchone()
            return dict(row) if row else {"n": 0, "wins": 0, "pnl": 0.0}
