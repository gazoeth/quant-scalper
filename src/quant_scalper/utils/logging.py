"""Centralised loguru configuration."""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

_CONFIGURED = False


def configure(level: str = "INFO", logfile: Path | None = None) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    logger.remove()
    logger.add(
        sys.stderr,
        level=level,
        colorize=True,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <7}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
        "<level>{message}</level>",
    )
    if logfile is not None:
        logfile.parent.mkdir(parents=True, exist_ok=True)
        logger.add(
            logfile,
            level=level,
            rotation="50 MB",
            retention=10,
            enqueue=True,
            backtrace=True,
            diagnose=False,
        )
    _CONFIGURED = True


__all__ = ["logger", "configure"]
