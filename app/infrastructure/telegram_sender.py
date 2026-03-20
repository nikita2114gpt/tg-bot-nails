from app.domain.models import OutboxEvent
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from app.presentation.callback.reminder_callbacks import build_reminder_cancel, build_reminder_confirm


class TelegramSender:
    def __init__(self, bot, admin_ids: list[int]):
        self.bot = bot
        self.admin_ids = [int(x) for x in admin_ids if x is not None]

    async def send_admin_notify(self, event: OutboxEvent):
        def _fmt_slot(raw: str) -> str:
            if isinstance(raw, str) and len(raw) >= 13 and "T" in raw:
                d, t = raw.split("T", 1)
                if len(d) == 8 and len(t) >= 4 and d.isdigit() and t[:4].isdigit():
                    return f"{d[6:8]}.{d[4:6]}.{d[0:4]} {t[:2]}:{t[2:4]}"
            return raw or "—"
        p = event.payload
        event_kind = str(p.get("event_kind") or "new_booking")
        service_id = p.get("service_id", "")
        start_datetime_utc = p.get("start_datetime_utc", "")
        customer_name = p.get("customer_name", "")
        phone_e164 = p.get("phone_e164", "")
        title_map = {
            "new_booking": "📌 Новая запись",
            "client_cancelled": "⚠️ Клиент отменил запись",
            "admin_cancelled": "⚠️ Админ отменил запись",
            "client_confirmed": "✅ Клиент подтвердил запись",
            "client_not_confirmed": "❗ Клиент не подтвердил запись",
            "appointment_edited": "✏️ Запись отредактирована",
        }
        title = title_map.get(event_kind, "📌 Событие по записи")
        text = (
            f"{title}\n\n"
            f"Услуга: {service_id}\n"
            f"Дата/время: {_fmt_slot(start_datetime_utc)}\n"
            f"Клиент: {customer_name}\n"
            f"Телефон: {phone_e164}"
        )

        for admin_id in self.admin_ids:
            await self.bot.send_message(admin_id, text)

    async def send_client_reminder(self, event: OutboxEvent):
        def _fmt_slot(raw: str) -> str:
            if isinstance(raw, str) and len(raw) >= 13 and "T" in raw:
                d, t = raw.split("T", 1)
                if len(d) == 8 and len(t) >= 4 and d.isdigit() and t[:4].isdigit():
                    return f"{d[6:8]}.{d[4:6]}.{d[0:4]} {t[:2]}:{t[2:4]}"
            return raw or "—"
        p = event.payload
        reminder_kind = str(p.get("reminder_kind") or "")

        service_id = p.get("service_id", "")
        start_datetime_utc = p.get("start_datetime_utc", "")
        user_id = p.get("user_id")
        try:
            user_id = int(user_id) if user_id is not None else None
        except Exception:
            user_id = None

        if user_id is None:
            return

        if reminder_kind == "reactivation":
            text = (
                "💬 Прошёл месяц с вашей последней записи.\n\n"
                "Самое время записаться снова."
            )
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="📅 Записаться", callback_data="c1|book")]
                ]
            )
            await self.bot.send_message(user_id, text, reply_markup=kb)
            return

        hours_before = p.get("hours_before")
        try:
            hours_before = int(hours_before) if hours_before is not None else None
        except Exception:
            hours_before = None
        appointment_id = str(p.get("appointment_id") or "")
        phone_e164 = str(p.get("phone_e164") or "")

        if hours_before == 24:
            text = (
                "⏰ Напоминание о записи (за 24 часа)\n\n"
                f"Услуга: {service_id}\n"
                f"Дата/время: {_fmt_slot(start_datetime_utc)}\n\n"
                "Подтвердите, пожалуйста, что запись актуальна."
            )
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="✅ Подтверждаю", callback_data=build_reminder_confirm(appointment_id))],
                    [InlineKeyboardButton(text="❌ Отменить запись", callback_data=build_reminder_cancel(appointment_id))],
                    [InlineKeyboardButton(text="📞 Позвонить", url=f"tel:{phone_e164}")],
                ]
            )
            await self.bot.send_message(user_id, text, reply_markup=kb)
            return

        text = (
            "⏰ Напоминание о записи\n\n"
            f"Услуга: {service_id}\n"
            f"Дата/время: {_fmt_slot(start_datetime_utc)}"
        )
        await self.bot.send_message(user_id, text)