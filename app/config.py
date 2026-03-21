import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    bot_token: str
    admin_ids: list[int]
    services: list[str]

    # тест: разрешить пользователю несколько активных CONFIRMED (слоты по-прежнему защищены)
    allow_multiple_active_bookings: bool

    # настройки слотов (пока простые)
    slot_duration_minutes: int
    max_days_ahead: int

    # клиентские карточки / витрина
    salon_address: str
    salon_contacts: str
    service_price_text: dict[str, str]
    service_duration_text: dict[str, str]


def _parse_admin_ids(raw: str) -> list[int]:
    if not raw:
        return []
    return [int(x.strip()) for x in raw.split(",") if x.strip()]


def _parse_services(raw: str) -> list[str]:
    if not raw:
        return ["Стрижка", "Маникюр", "Массаж"]
    return [x.strip() for x in raw.split(",") if x.strip()]


def _parse_parallel_map(services: list[str], raw: str, default: str) -> dict[str, str]:
    if not raw.strip():
        return {s: default for s in services}
    parts = [x.strip() for x in raw.split(",")]
    out: dict[str, str] = {}
    for i, s in enumerate(services):
        out[s] = parts[i] if i < len(parts) and parts[i] else default
    return out


def load_settings() -> Settings:
    bot_token = os.getenv("BOT_TOKEN")

    if not bot_token:
        raise ValueError("❌ BOT_TOKEN не найден в .env")

    admin_ids = _parse_admin_ids(os.getenv("ADMIN_IDS", ""))
    services = _parse_services(os.getenv("SERVICES", ""))
    slot_duration_minutes = int(os.getenv("SLOT_DURATION_MINUTES", 60))

    default_dur = f"{slot_duration_minutes} мин"
    price_map = _parse_parallel_map(
        services,
        os.getenv("SERVICE_PRICES", ""),
        "—",
    )
    dur_map = _parse_parallel_map(
        services,
        os.getenv("SERVICE_DURATIONS_MIN", ""),
        default_dur,
    )

    allow_multi = (os.getenv("ALLOW_MULTIPLE_ACTIVE_BOOKINGS", "") or "").strip().lower()
    allow_multiple_active_bookings = allow_multi in ("1", "true", "yes", "on")

    return Settings(
        bot_token=bot_token,
        admin_ids=admin_ids,
        services=services,
        allow_multiple_active_bookings=allow_multiple_active_bookings,
        slot_duration_minutes=slot_duration_minutes,
        max_days_ahead=int(os.getenv("MAX_DAYS_AHEAD", 7)),
        salon_address=os.getenv("SALON_ADDRESS", "Адрес уточняйте у администратора.").strip()
        or "Адрес уточняйте у администратора.",
        salon_contacts=os.getenv("SALON_CONTACTS", "").strip()
        or "Контакты уточняйте у администратора.",
        service_price_text=price_map,
        service_duration_text=dur_map,
    )