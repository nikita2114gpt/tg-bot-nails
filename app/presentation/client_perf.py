"""Точечные замеры клиентского UI. Включение: BOT_CLIENT_PERF_LOG=1 (stdout, flush)."""

from __future__ import annotations

import os
import time

from app.infrastructure.logging import get_logger

logger = get_logger(__name__)


def is_client_perf_enabled() -> bool:
    v = (os.getenv("BOT_CLIENT_PERF_LOG") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


class PerfSpan:
    """Один сценарий: phase даёт Δ от предыдущей метки и total от входа."""

    __slots__ = ("_flow", "_start", "_last")

    def __init__(self, flow: str) -> None:
        self._flow = flow
        now = time.perf_counter()
        self._start = now
        self._last = now

    def mark(self, phase: str) -> None:
        if not is_client_perf_enabled():
            return
        now = time.perf_counter()
        delta_ms = (now - self._last) * 1000
        total_ms = (now - self._start) * 1000
        self._last = now
        logger.info(
            "PERF [%s] %s  +%.1fms  (total %.1fms)",
            self._flow,
            phase,
            delta_ms,
            total_ms,
        )
