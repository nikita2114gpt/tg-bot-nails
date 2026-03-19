import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    bot_token: str
    admin_ids: list[int]
    services: list[str]

    # настройки слотов (пока простые)
    slot_duration_minutes: int
    max_days_ahead: int


def _parse_admin_ids(raw: str) -> list[int]:
    if not raw:
        return []
    return [int(x.strip()) for x in raw.split(",") if x.strip()]


def _parse_services(raw: str) -> list[str]:
    if not raw:
        return ["Стрижка", "Маникюр", "Массаж"]
    return [x.strip() for x in raw.split(",") if x.strip()]


def load_settings() -> Settings:
    bot_token = os.getenv("BOT_TOKEN")

    if not bot_token:
        raise ValueError("❌ BOT_TOKEN не найден в .env")

    admin_ids = _parse_admin_ids(os.getenv("ADMIN_IDS", ""))
    services = _parse_services(os.getenv("SERVICES", ""))

    return Settings(
        bot_token=bot_token,
        admin_ids=admin_ids,
        services=services,
        slot_duration_minutes=int(os.getenv("SLOT_DURATION_MINUTES", 60)),
        max_days_ahead=int(os.getenv("MAX_DAYS_AHEAD", 7)),
    )