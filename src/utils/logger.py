from __future__ import annotations

import sys
from datetime import datetime

from loguru import logger

from src.utils.storage import project_path

_CONFIGURED = False


def setup_logger():
    """Configure console and daily file logs once per process."""
    global _CONFIGURED
    if _CONFIGURED:
        return logger

    log_dir = project_path("data/logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{datetime.now():%Y-%m-%d}.log"

    logger.remove()
    logger.add(
        sys.stderr,
        level="INFO",
        colorize=sys.stderr.isatty(),
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
    )
    logger.add(
        log_file,
        level="DEBUG",
        encoding="utf-8",
        rotation="00:00",
        retention="30 days",
        enqueue=True,
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message}",
    )
    _CONFIGURED = True
    return logger
