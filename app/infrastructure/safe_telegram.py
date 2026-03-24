from __future__ import annotations

from aiogram.types import CallbackQuery

from app.infrastructure.logging import get_logger
from app.infrastructure.network_retry import retry_network_operation

logger = get_logger(__name__)


async def safe_send_message(
    *,
    bot,
    chat_id: int,
    text: str,
    **kwargs,
) -> None:
    async def _op() -> object:
        return await bot.send_message(chat_id=chat_id, text=text, **kwargs)

    await retry_network_operation(_op, operation_name="safe_send_message")


async def safe_edit_message(
    *,
    callback: CallbackQuery,
    text: str,
    **kwargs,
) -> None:
    if callback.message is None:
        return

    async def _op() -> object:
        return await callback.message.edit_text(text=text, **kwargs)

    await retry_network_operation(_op, operation_name="safe_edit_message")


async def safe_answer_callback(
    callback: CallbackQuery,
    text: str | None = None,
    *,
    show_alert: bool = False,
    cache_time: int = 0,
) -> None:
    answered = False

    async def _op() -> object:
        nonlocal answered
        answered = True
        return await callback.answer(
            text=text,
            show_alert=show_alert,
            cache_time=cache_time,
        )

    result = await retry_network_operation(_op, operation_name="safe_answer_callback")
    if result is not None:
        return

    if answered:
        return

    # Last-resort acknowledge: avoids "query is too old" noise in client.
    try:
        await callback.answer()
    except Exception:
        logger.debug("safe_answer_callback: fallback callback.answer failed", exc_info=True)
