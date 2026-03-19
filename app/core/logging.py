from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class LogContext:
    """
    Context fields to attach to logs.

    In full production you would use structured logging (JSON) and correlation IDs.
    """

    correlation_id: str
    user_id: int | None = None
    draft_id: str | None = None
    appointment_id: str | None = None


def configure_logging(log_level: str = "INFO") -> None:
    """
    Minimal logging setup for the MVP.
    """

    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def new_correlation_id() -> str:
    return uuid.uuid4().hex


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)

