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
_SERVICE_NAME = os.getenv("SERVICE_NAME", "tgbot")


class TelegramExceptionAlertHandler(logging.Handler):
    def __init__(self, bot: Bot, admin_ids: list[int]) -> None:
        super().__init__(level=logging.ERROR)
        self._bot = bot
        self._admin_ids = admin_ids

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno < logging.ERROR:
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        payload = self._build_payload(record)
        loop.create_task(self._safe_send(payload))

    def _build_payload(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        exception_summary = "-"
        if record.exc_info and record.exc_info[1] is not None:
            exception_summary = f"{type(record.exc_info[1]).__name__}: {record.exc_info[1]}"
        elif message:
            exception_summary = message

        payload = (
            f"ERROR ALERT\n"
            f"service={_SERVICE_NAME}\n"
            f"logger={record.name}\n"
            f"exception={exception_summary}"
        )
        if len(payload) > 3500:
            return f"{payload[:3500]}..."
        return payload

    async def _safe_send(self, payload: str) -> None:
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
    root_logger.propagate = False

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
    handler.setLevel(logging.ERROR)
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
