from __future__ import annotations

import logging
import os

_DEFAULT_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def configure_logging(log_level: str | None = None) -> None:
    resolved_level = (log_level or os.getenv("LOG_LEVEL", "INFO")).upper().strip()
    level = getattr(logging, resolved_level, logging.INFO)

    logging.basicConfig(
        level=level,
        format=_DEFAULT_LOG_FORMAT,
    )


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
