from __future__ import annotations

import traceback

from aiogram import Router
from aiogram.filters import Command, StateFilter
from aiogram.types import Message

from app.infrastructure.logging import get_logger
from app.presentation.fsm.states import AdminStates
from aiogram.types.error_event import ErrorEvent

router = Router(name="common")
logger = get_logger(__name__)


@router.message(Command("help"), ~StateFilter(AdminStates))
async def help_handler(message: Message) -> None:
    """Minimal help for MVP v2."""

    await message.answer(
        "Я бот для записи на услуги.\n"
        "Команды: /start (главное меню), /help\n"
        "Если что-то пошло не так — попробуйте /start ещё раз."
    )


@router.error()
async def on_error(event: ErrorEvent, bot: object | None = None) -> None:  # bot type depends on aiogram internals
    """
    Global error handler (for scaffolding).

    Production: should map domain errors to user messages and log stacktraces.
    """

    try:
        err_text = str(event.exception)
    except Exception:
        err_text = "internal error"

    try:
        tb = "".join(
            traceback.format_exception(
                type(event.exception),
                event.exception,
                event.exception.__traceback__,
            )
        )
    except Exception:
        tb = f"{type(event.exception).__name__}: {err_text}"
    update = event.update
    user_id: int | None = None
    handler_name = "unknown"
    payload = ""
    try:
        if getattr(update, "message", None) and getattr(update.message, "from_user", None):
            user_id = update.message.from_user.id
            payload = update.message.text or ""
        elif getattr(update, "callback_query", None):
            cq = update.callback_query
            if getattr(cq, "from_user", None):
                user_id = cq.from_user.id
            payload = cq.data or ""
    except Exception:
        pass

    try:
        extra_data = getattr(event, "extra_data", {}) or {}
        handler_candidate = extra_data.get("handler")
        if handler_candidate is not None:
            handler_name = str(handler_candidate)
    except Exception:
        pass

    logger.exception(
        "GLOBAL_HANDLER_ERROR type=%s handler=%s user_id=%s payload=%s error=%s\n%s",
        type(event.exception).__name__,
        handler_name,
        user_id,
        payload,
        err_text,
        tb.rstrip(),
    )

    text = "Произошла ошибка при выполнении команды. Попробуйте ещё раз."

    # Best-effort attempt to reply to the user.
    if getattr(update, "message", None):
        await update.message.answer(text)
        return
    if getattr(update, "callback_query", None) and getattr(update.callback_query, "message", None):
        await update.callback_query.message.answer(text)

