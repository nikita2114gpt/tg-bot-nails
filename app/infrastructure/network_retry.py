from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, TypeVar

from aiohttp import ClientError
from aiogram.exceptions import TelegramNetworkError

from app.infrastructure.logging import get_logger

T = TypeVar("T")

logger = get_logger(__name__)

_RETRY_DELAYS_SECONDS = (1, 2, 4, 8, 16)


async def retry_network_operation(
    operation: Callable[[], Awaitable[T]],
    *,
    operation_name: str,
    max_attempts: int = 5,
) -> T | None:
    attempts = max(1, min(max_attempts, len(_RETRY_DELAYS_SECONDS)))
    for attempt in range(1, attempts + 1):
        try:
            result = await operation()
            if attempt > 1:
                logger.info(
                    "RETRY_SUCCESS operation=%s attempt=%s",
                    operation_name,
                    attempt,
                )
            return result
        except (TelegramNetworkError, ClientError, asyncio.TimeoutError) as exc:
            logger.warning(
                "NETWORK_ERROR operation=%s attempt=%s error=%s",
                operation_name,
                attempt,
                exc,
            )
            if attempt >= attempts:
                logger.error(
                    "RETRY_FAILED operation=%s attempts=%s",
                    operation_name,
                    attempts,
                    exc_info=True,
                )
                return None

            delay = _RETRY_DELAYS_SECONDS[attempt - 1]
            logger.info(
                "RETRY_ATTEMPT operation=%s next_attempt=%s delay_seconds=%s",
                operation_name,
                attempt + 1,
                delay,
            )
            await asyncio.sleep(delay)
