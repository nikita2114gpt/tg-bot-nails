from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


class RuntimeLockError(RuntimeError):
    """Raised when another bot process already holds runtime lock."""


class RuntimeLock:
    """
    Single-instance process lock for Linux hosts.

    Uses advisory flock so only one polling process can run.
    """

    def __init__(self, lock_file: str | Path) -> None:
        self._lock_path = Path(lock_file)
        self._lock_fd = None
        self._fcntl = None

    def acquire(self) -> None:
        try:
            import fcntl  # type: ignore
        except ImportError:
            # Non-POSIX environment (e.g. local Windows dev) - skip lock.
            logger.warning("runtime-lock: fcntl unavailable, single-instance lock disabled")
            return

        self._fcntl = fcntl
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_fd = self._lock_path.open("w", encoding="utf-8")
        try:
            self._fcntl.flock(self._lock_fd.fileno(), self._fcntl.LOCK_EX | self._fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeLockError(f"another process already holds lock: {self._lock_path}") from exc

        self._lock_fd.seek(0)
        self._lock_fd.truncate()
        self._lock_fd.write(str(os.getpid()))
        self._lock_fd.flush()

    def release(self) -> None:
        if self._lock_fd is None or self._fcntl is None:
            return
        try:
            self._fcntl.flock(self._lock_fd.fileno(), self._fcntl.LOCK_UN)
        finally:
            self._lock_fd.close()
            self._lock_fd = None
