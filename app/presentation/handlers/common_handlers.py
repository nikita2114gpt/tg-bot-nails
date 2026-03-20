from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.types.error_event import ErrorEvent

router = Router(name="common")


@router.message(Command("help"))
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

    text = "Произошла ошибка при выполнении команды. Попробуйте ещё раз."

    update = event.update
    # Best-effort attempt to reply to the user.
    if getattr(update, "message", None):
        await update.message.answer(text)
        return
    if getattr(update, "callback_query", None) and getattr(update.callback_query, "message", None):
        await update.callback_query.message.answer(text)

