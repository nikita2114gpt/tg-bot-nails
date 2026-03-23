from __future__ import annotations

from typing import Optional


def format_duration_minutes(value: int) -> str:
    h = value // 60
    m = value % 60
    if h > 0 and m > 0:
        return f"{h} ч {m} мин"
    if h > 0:
        return f"{h} ч"
    return f"{m} мин"


def resolve_service_price_and_duration(
    service_repo,
    service_id: str,
) -> tuple[str, str]:
    if service_repo is None:
        return "—", "—"
    lookup = (service_id or "").strip().lower()
    try:
        for item in service_repo.list_all():
            item_name = (getattr(item, "name", "") or "").strip().lower()
            item_id = (getattr(item, "service_id", "") or "").strip().lower()
            if item_name != lookup and item_id != lookup:
                continue
            price = (getattr(item, "price_text", "") or "").strip() or "—"
            duration_minutes = int(getattr(item, "duration_minutes", 0) or 0)
            duration_text = format_duration_minutes(duration_minutes) if duration_minutes > 0 else "—"
            return price, duration_text
    except Exception:
        return "—", "—"
    return "—", "—"


def resolve_service_price_and_duration_optional(
    service_repo,
    service_id: str,
) -> tuple[Optional[str], Optional[str]]:
    if service_repo is None:
        return None, None
    lookup = (service_id or "").strip().lower()
    try:
        for item in service_repo.list_all():
            item_name = (getattr(item, "name", "") or "").strip().lower()
            item_id = (getattr(item, "service_id", "") or "").strip().lower()
            if item_name != lookup and item_id != lookup:
                continue
            price = (getattr(item, "price_text", "") or "").strip() or None
            duration_minutes = int(getattr(item, "duration_minutes", 0) or 0)
            duration_text = format_duration_minutes(duration_minutes) if duration_minutes > 0 else None
            return price, duration_text
    except Exception:
        return None, None
    return None, None


def format_service_block(service_name: str, price: str, duration: str) -> str:
    return "\n".join(
        [
            f"💇 Услуга: {service_name or '—'}",
            f"💰 Цена: {price or '—'}",
            f"⏱ Длительность: {duration or '—'}",
        ]
    )
