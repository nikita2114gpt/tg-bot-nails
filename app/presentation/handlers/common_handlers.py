from __future__ import annotations

import logging
import traceback

from aiogram import Router
from aiogram.filters import Command, StateFilter
from aiogram.types import Message

from app.presentation.fsm.states import AdminStates
from aiogram.types.error_event import ErrorEvent

router = Router(name="common")


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
    _log.error("handler error:\n%s", tb.rstrip())

    text = "Произошла ошибка при выполнении команды. Попробуйте ещё раз."

    update = event.update
    # Best-effort attempt to reply to the user.
    if getattr(update, "message", None):
        await update.message.answer(text)
        return
    if getattr(update, "callback_query", None) and getattr(update.callback_query, "message", None):
        await update.callback_query.message.answer(text)

