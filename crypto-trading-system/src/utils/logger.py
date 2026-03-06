"""Application logging configuration."""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_MAX_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 3
_PAYLOAD_LOGGER_NAME = "run_payload"


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _log_file_path() -> Path:
    return _project_root() / "logs" / "app.log"


def setup_logging() -> None:
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    log_path = _log_file_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(_FORMAT)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.handlers.clear()
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    payload_logger = logging.getLogger(_PAYLOAD_LOGGER_NAME)
    payload_logger.setLevel(level)
    payload_logger.propagate = False
    payload_logger.handlers.clear()
    payload_logger.addHandler(file_handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def get_payload_logger() -> logging.Logger:
    return logging.getLogger(_PAYLOAD_LOGGER_NAME)
