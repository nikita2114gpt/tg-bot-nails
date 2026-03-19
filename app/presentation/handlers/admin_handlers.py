from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.config import Settings
from app.infrastructure.storage_filejson import AppointmentRepository

router = Router(name="admin")


@router.message(Command("admin"))
async def admin_entry(
    message: Message,
    settings: Settings,
    appointment_repo: AppointmentRepository,
) -> None:
    """Minimal admin entrypoint: list appointments."""

    user_id = message.from_user.id if message.from_user else None
    if user_id is None or user_id not in set(settings.admin_ids):
        await message.answer("Недостаточно прав.")
        return

    appointments = appointment_repo.list_all()
    if not appointments:
        await message.answer("Пока нет записей.")
        return

    lines: list[str] = ["Записи:"]
    for ap in appointments[:20]:
        lines.append(
            f"- {ap.customer_name} | {ap.service_id} | {ap.start_datetime_utc} | status={ap.status.value}"
        )

    await message.answer("\n".join(lines))

