from __future__ import annotations

import asyncio
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

_DEFAULT_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_MAX_LOG_BYTES = 10 * 1024 * 1024
_BACKUP_COUNT = 5


class TelegramExceptionAlertHandler(logging.Handler):
    def __init__(self, bot: Bot, admin_ids: list[int]) -> None:
        super().__init__(level=logging.ERROR)
        self._bot = bot
        self._admin_ids = admin_ids

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno < logging.ERROR or record.exc_info is None:
            return

        try:
            message = self.format(record)
        except Exception:
            message = f"{record.name}: {record.getMessage()}"

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        loop.create_task(self._safe_send(message))

    async def _safe_send(self, message: str) -> None:
        text = message
        if len(text) > 3500:
            text = f"{text[:3500]}..."
        payload = f"ERROR EXCEPTION\n{text}"
        for admin_id in self._admin_ids:
            try:
                await self._bot.send_message(chat_id=admin_id, text=payload)
            except TelegramAPIError:
                continue
            except Exception:
                continue


def configure_logging(log_level: str | None = None) -> None:
    resolved_level = (log_level or os.getenv("LOG_LEVEL", "INFO")).upper().strip()
    level = getattr(logging, resolved_level, logging.INFO)
    logs_dir = Path(os.getenv("BOT_LOG_DIR", "logs"))
    logs_dir.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(_DEFAULT_LOG_FORMAT)
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.handlers.clear()

    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(level)
    stream_handler.setFormatter(formatter)

    info_file_handler = RotatingFileHandler(
        filename=str(logs_dir / "bot.log"),
        maxBytes=_MAX_LOG_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    info_file_handler.setLevel(level)
    info_file_handler.setFormatter(formatter)

    error_file_handler = RotatingFileHandler(
        filename=str(logs_dir / "bot.error.log"),
        maxBytes=_MAX_LOG_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    error_file_handler.setLevel(logging.ERROR)
    error_file_handler.setFormatter(formatter)

    root_logger.addHandler(stream_handler)
    root_logger.addHandler(info_file_handler)
    root_logger.addHandler(error_file_handler)


def attach_telegram_alerts(bot: Bot, admin_ids: list[int]) -> None:
    if not admin_ids:
        return

    root_logger = logging.getLogger()
    root_logger.handlers = [
        existing_handler
        for existing_handler in root_logger.handlers
        if not isinstance(existing_handler, TelegramExceptionAlertHandler)
    ]

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    handler = TelegramExceptionAlertHandler(bot=bot, admin_ids=admin_ids)
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
