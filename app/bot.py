from __future__ import annotations

from aiogram import Bot

from app.config import Settings


def create_bot(settings: Settings) -> Bot:
    """
    Creates a Telegram Bot instance.

    Keep this factory thin; anything business-related stays outside.
    """

    return Bot(token=settings.bot_token)

