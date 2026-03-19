from app.domain.models import OutboxEvent


class TelegramSender:
    def __init__(self, bot, admin_ids: list[int]):
        self.bot = bot
        self.admin_ids = [int(x) for x in admin_ids if x is not None]

    async def send_admin_notify(self, event: OutboxEvent):
        p = event.payload

        service_id = p.get("service_id", "")
        start_datetime_utc = p.get("start_datetime_utc", "")
        customer_name = p.get("customer_name", "")
        phone_e164 = p.get("phone_e164", "")

        text = (
            "📌 Новая запись\n\n"
            f"Услуга: {service_id}\n"
            f"Дата/время: {start_datetime_utc}\n"
            f"Клиент: {customer_name}\n"
            f"Телефон: {phone_e164}"
        )

        for admin_id in self.admin_ids:
            await self.bot.send_message(admin_id, text)

    async def send_client_reminder(self, event: OutboxEvent):
        p = event.payload

        service_id = p.get("service_id", "")
        start_datetime_utc = p.get("start_datetime_utc", "")
        user_id = p.get("user_id")
        try:
            user_id = int(user_id) if user_id is not None else None
        except Exception:
            user_id = None

        if user_id is None:
            return

        text = (
            "⏰ Напоминание о записи\n\n"
            f"Услуга: {service_id}\n"
            f"Дата/время: {start_datetime_utc}"
        )

        await self.bot.send_message(user_id, text)