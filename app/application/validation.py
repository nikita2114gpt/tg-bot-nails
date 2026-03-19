import re
from datetime import datetime, date


PHONE_REGEX = re.compile(r"^\+?[1-9]\d{9,14}$")
NAME_REGEX = re.compile(r"^[A-Za-zА-Яа-яЁё\s\-]{2,50}$")


def validate_service_id(service_id: str, allowed_services: list[str]) -> bool:
    return service_id in allowed_services


def validate_date_string(value: str) -> bool:
    try:
        datetime.strptime(value, "%Y%m%d")
        return True
    except ValueError:
        return False


def validate_time_string(value: str) -> bool:
    try:
        datetime.strptime(value, "%H%M")
        return True
    except ValueError:
        return False


def validate_not_past_date(value: str) -> bool:
    try:
        selected_date = datetime.strptime(value, "%Y%m%d").date()
        return selected_date >= date.today()
    except ValueError:
        return False


def normalize_phone(phone: str) -> str:
    cleaned = re.sub(r"[^\d+]", "", phone).strip()

    if cleaned.startswith("8") and len(cleaned) == 11:
        cleaned = "+7" + cleaned[1:]
    elif cleaned.startswith("7") and len(cleaned) == 11:
        cleaned = "+" + cleaned
    elif not cleaned.startswith("+"):
        cleaned = "+" + cleaned

    return cleaned


def validate_phone(phone: str) -> bool:
    normalized = normalize_phone(phone)
    return bool(PHONE_REGEX.fullmatch(normalized))


def validate_name(name: str) -> bool:
    name = name.strip()
    return bool(NAME_REGEX.fullmatch(name))