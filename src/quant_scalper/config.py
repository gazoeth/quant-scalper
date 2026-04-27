"""Application configuration loaded from environment variables.

All settings are validated by pydantic so that misconfigured deployments fail fast
rather than mid-trade.
"""

from __future__ import annotations

from enum import Enum

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class RunMode(str, Enum):
    TESTNET = "testnet"
    PAPER = "paper"
    LIVE = "live"


class Settings(BaseSettings):
    """Top-level configuration. Reads `.env` automatically when present."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ─── Mode / API ─────────────────────────────────────────────
    mode: RunMode = Field(default=RunMode.TESTNET, alias="QS_MODE")
    binance_api_key: str = Field(default="", alias="BINANCE_API_KEY")
    binance_api_secret: str = Field(default="", alias="BINANCE_API_SECRET")

    # ─── Strategy ──────────────────────────────────────────────
    timeframe: str = Field(default="15m", alias="QS_TIMEFRAME")
    mtf_timeframe: str = Field(default="1h", alias="QS_MTF_TIMEFRAME")
    max_open_positions: int = Field(default=5, alias="QS_MAX_OPEN_POSITIONS")
    scan_interval_seconds: int = Field(default=60, alias="QS_SCAN_INTERVAL_SECONDS")

    major_symbols_csv: str = Field(
        default="BTC/USDT,ETH/USDT,SOL/USDT,BNB/USDT,XRP/USDT,DOGE/USDT,ADA/USDT,AVAX/USDT,LINK/USDT,TON/USDT",
        alias="QS_MAJOR_SYMBOLS",
    )

    major_leverage: int = Field(default=10, alias="QS_MAJOR_LEVERAGE")
    alt_leverage: int = Field(default=3, alias="QS_ALT_LEVERAGE")
    major_margin_usdt: float = Field(default=20.0, alias="QS_MAJOR_MARGIN_USDT")
    alt_margin_usdt: float = Field(default=10.0, alias="QS_ALT_MARGIN_USDT")

    stop_loss_pct: float = Field(default=0.008, alias="QS_STOP_LOSS_PCT")
    take_profit_pct: float = Field(default=0.015, alias="QS_TAKE_PROFIT_PCT")
    trailing_trigger_pct: float = Field(default=0.006, alias="QS_TRAILING_TRIGGER_PCT")
    trailing_distance_pct: float = Field(default=0.004, alias="QS_TRAILING_DISTANCE_PCT")

    # ─── Reporting ─────────────────────────────────────────────
    telegram_bot_token: str | None = Field(default=None, alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str | None = Field(default=None, alias="TELEGRAM_CHAT_ID")
    pnl_report_interval_min: int = Field(default=60, alias="QS_PNL_REPORT_INTERVAL_MIN")

    # ─── Storage / Logging ─────────────────────────────────────
    database_url: str = Field(default="sqlite:///data/quant_scalper.db", alias="QS_DATABASE_URL")
    log_level: str = Field(default="INFO", alias="QS_LOG_LEVEL")

    @field_validator("major_symbols_csv")
    @classmethod
    def _normalize_symbols(cls, v: str) -> str:
        return ",".join(s.strip().upper() for s in v.split(",") if s.strip())

    @property
    def major_symbols(self) -> list[str]:
        return [s for s in self.major_symbols_csv.split(",") if s]

    @property
    def is_live(self) -> bool:
        return self.mode is RunMode.LIVE

    @property
    def is_testnet(self) -> bool:
        return self.mode is RunMode.TESTNET

    @property
    def is_paper(self) -> bool:
        return self.mode is RunMode.PAPER

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    def leverage_for(self, symbol: str) -> int:
        return self.major_leverage if symbol in self.major_symbols else self.alt_leverage

    def margin_for(self, symbol: str) -> float:
        return self.major_margin_usdt if symbol in self.major_symbols else self.alt_margin_usdt


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return cached settings, lazily loaded on first call."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings_cache() -> None:
    """Test hook: reload settings on next call to ``get_settings``."""
    global _settings
    _settings = None
