from __future__ import annotations


def build_reminder_confirm(appointment_id: str) -> str:
    return f"r1|ok|{appointment_id}"


def build_reminder_cancel(appointment_id: str) -> str:
    return f"r1|cx|{appointment_id}"


def parse_reminder_callback(raw: str) -> tuple[str, str]:
    if not raw or not isinstance(raw, str):
        raise ValueError("bad callback")
    parts = raw.split("|")
    if len(parts) != 3 or parts[0] != "r1" or not parts[1] or not parts[2]:
        raise ValueError("bad callback")
    return parts[1], parts[2]
