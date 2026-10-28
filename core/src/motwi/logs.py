"""Rotating, size-capped log file (spec 7)."""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_BYTES = 1_000_000
LOG_BACKUPS = 5


def setup_logging(directory: Path, level: int = logging.INFO) -> Path:
    path = directory / "motwi.log"
    handler = RotatingFileHandler(
        path, maxBytes=LOG_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)
    return path
