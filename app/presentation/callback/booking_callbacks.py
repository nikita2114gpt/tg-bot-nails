from typing import Optional


CALLBACK_PREFIX = "b2"
CALLBACK_VERSION = "v2"

ALLOWED_ACTIONS = {"svc", "date", "time", "confirm", "cancel", "bk"}


def build_callback(action: str, draft_id: str, payload: Optional[str] = None) -> str:
    parts = [CALLBACK_PREFIX, CALLBACK_VERSION, action, draft_id]

    if payload is not None:
        parts.append(payload)

    return ":".join(parts)


def parse_callback_data(raw: str) -> dict:
    if not raw or not isinstance(raw, str):
        raise ValueError("Некорректный callback payload")

    parts = raw.split(":")

    if len(parts) < 4:
        raise ValueError("Некорректный callback payload")

    prefix, version, action, draft_id = parts[:4]
    payload = parts[4] if len(parts) > 4 else None

    if prefix != CALLBACK_PREFIX:
        raise ValueError("Некорректный callback prefix")

    if version != CALLBACK_VERSION:
        raise ValueError("Некорректная версия callback")

    if action not in ALLOWED_ACTIONS:
        raise ValueError("Неизвестное действие")

    if not draft_id:
        raise ValueError("Пустой draft_id")

    return {
        "action": action,
        "draft_id": draft_id,
        "payload": payload,
    }