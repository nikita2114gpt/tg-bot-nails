import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from app.domain.enums import DraftStep, AppointmentStatus, OutboxType, OutboxStatus
from app.domain.models import BookingDraft, Appointment, OutboxEvent


BASE_DIR = Path(__file__).resolve().parents[2]
DATA_FILE = BASE_DIR / "data" / "storage.json"
DATA_LOCK = threading.RLock()


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


def _ensure_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _parse_enum(enum_cls: type[Any], raw: Any, default: Any) -> Any:
    try:
        if isinstance(raw, enum_cls):
            return raw
        return enum_cls(raw)
    except Exception:
        return default


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _load_data():
    with DATA_LOCK:
        if not DATA_FILE.exists():
            return {"drafts": [], "appointments": [], "outbox": []}

        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except json.JSONDecodeError:
            # Storage file may be partially written. Backup and continue.
            backup_name = f"storage.json.bak.{datetime.utcnow().strftime('%Y%m%dT%H%M%S')}"
            try:
                DATA_FILE.replace(DATA_FILE.with_name(backup_name))
            except Exception:
                # If rename fails, still fallback to empty storage.
                pass
            return {"drafts": [], "appointments": [], "outbox": []}
        except Exception:
            return {"drafts": [], "appointments": [], "outbox": []}

        if not isinstance(raw, dict):
            return {"drafts": [], "appointments": [], "outbox": []}

        return {
            "drafts": _ensure_list(raw.get("drafts")),
            "appointments": _ensure_list(raw.get("appointments")),
            "outbox": _ensure_list(raw.get("outbox")),
        }


def _save_data(data):
    with DATA_LOCK:
        DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = DATA_FILE.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()

        os.replace(tmp_path, DATA_FILE)


def _draft_to_dict(draft: BookingDraft) -> dict:
    return {
        "draft_id": draft.draft_id,
        "user_id": draft.user_id,
        "step": draft.step.value,
        "service_id": draft.service_id,
        "appointment_date": draft.appointment_date,
        "appointment_time": draft.appointment_time,
        "start_datetime_utc": draft.start_datetime_utc,
        "customer_name": draft.customer_name,
        "phone_e164": draft.phone_e164,
        "created_at": draft.created_at,
        "updated_at": draft.updated_at,
    }


def _draft_from_dict(data: dict) -> Optional[BookingDraft]:
    if not isinstance(data, dict):
        return None

    draft_id = data.get("draft_id")
    user_id_raw = data.get("user_id")
    if not draft_id or not isinstance(draft_id, str):
        return None
    try:
        user_id = int(user_id_raw)
    except Exception:
        return None

    step = _parse_enum(
        DraftStep,
        data.get("step"),
        DraftStep.CANCELLED,
    )

    created_at = data.get("created_at")
    updated_at = data.get("updated_at")

    return BookingDraft(
        draft_id=draft_id,
        user_id=user_id,
        step=step,
        service_id=data.get("service_id"),
        appointment_date=data.get("appointment_date"),
        appointment_time=data.get("appointment_time"),
        start_datetime_utc=data.get("start_datetime_utc"),
        customer_name=data.get("customer_name"),
        phone_e164=data.get("phone_e164"),
        created_at=created_at if isinstance(created_at, str) else _now_iso(),
        updated_at=updated_at if isinstance(updated_at, str) else _now_iso(),
    )


def _appointment_to_dict(appointment: Appointment) -> dict:
    return {
        "appointment_id": appointment.appointment_id,
        "draft_id": appointment.draft_id,
        "user_id": appointment.user_id,
        "service_id": appointment.service_id,
        "start_datetime_utc": appointment.start_datetime_utc,
        "customer_name": appointment.customer_name,
        "phone_e164": appointment.phone_e164,
        "status": appointment.status.value,
        "created_at": appointment.created_at,
        "updated_at": appointment.updated_at,
    }


def _appointment_from_dict(data: dict) -> Optional[Appointment]:
    if not isinstance(data, dict):
        return None

    appointment_id = data.get("appointment_id")
    draft_id = data.get("draft_id")
    user_id_raw = data.get("user_id")
    if not appointment_id or not draft_id:
        return None
    if not isinstance(appointment_id, str) or not isinstance(draft_id, str):
        return None
    try:
        user_id = int(user_id_raw)
    except Exception:
        return None

    status = _parse_enum(
        AppointmentStatus,
        data.get("status"),
        AppointmentStatus.CANCELLED,
    )

    return Appointment(
        appointment_id=appointment_id,
        draft_id=draft_id,
        user_id=user_id,
        service_id=data.get("service_id") or "",
        start_datetime_utc=data.get("start_datetime_utc") or "",
        customer_name=data.get("customer_name") or "",
        phone_e164=data.get("phone_e164") or "",
        status=status,
        created_at=data.get("created_at") if isinstance(data.get("created_at"), str) else _now_iso(),
        updated_at=data.get("updated_at") if isinstance(data.get("updated_at"), str) else _now_iso(),
    )


def _outbox_to_dict(event: OutboxEvent) -> dict:
    return {
        "event_id": event.event_id,
        "event_type": event.event_type.value,
        "status": event.status.value,
        "idempotency_key": event.idempotency_key,
        "send_at": event.send_at,
        "payload": event.payload,
        "attempts": event.attempts,
        "last_error": event.last_error,
        "created_at": event.created_at,
        "updated_at": event.updated_at,
    }


def _outbox_from_dict(data: dict) -> Optional[OutboxEvent]:
    if not isinstance(data, dict):
        return None

    event_id = data.get("event_id")
    idempotency_key = data.get("idempotency_key")
    if not event_id or not idempotency_key:
        return None
    if not isinstance(event_id, str) or not isinstance(idempotency_key, str):
        return None

    event_type_raw = data.get("event_type")
    type_valid = True
    try:
        event_type = OutboxType(event_type_raw)
    except Exception:
        type_valid = False
        event_type = OutboxType.ADMIN_NOTIFY

    status_raw = data.get("status")
    status_valid = True
    try:
        status = OutboxStatus(status_raw)
    except Exception:
        status_valid = False
        status = OutboxStatus.FAILED

    if not type_valid:
        status = OutboxStatus.FAILED

    payload = data.get("payload", {})
    if not isinstance(payload, dict):
        payload = {}

    send_at = data.get("send_at")
    if send_at is not None and not isinstance(send_at, str):
        send_at = None

    attempts = data.get("attempts", 0)
    if not isinstance(attempts, int):
        attempts = 0

    last_error = data.get("last_error")
    if last_error is not None and not isinstance(last_error, str):
        last_error = None

    return OutboxEvent(
        event_id=event_id,
        event_type=event_type,
        status=status,
        idempotency_key=idempotency_key,
        send_at=send_at,
        payload=payload,
        attempts=attempts,
        last_error=last_error,
        created_at=data.get("created_at") if isinstance(data.get("created_at"), str) else _now_iso(),
        updated_at=data.get("updated_at") if isinstance(data.get("updated_at"), str) else _now_iso(),
    )


class DraftRepository:
    def save_draft(self, draft: BookingDraft):
        data = _load_data()

        drafts = [d for d in _ensure_list(data.get("drafts")) if isinstance(d, dict)]
        drafts = [d for d in drafts if d.get("draft_id") != draft.draft_id]
        drafts.append(_draft_to_dict(draft))

        data["drafts"] = drafts
        _save_data(data)

    def get_by_id(self, draft_id: str) -> Optional[BookingDraft]:
        data = _load_data()

        for d in _ensure_list(data.get("drafts")):
            if not isinstance(d, dict):
                continue
            if d.get("draft_id") == draft_id:
                return _draft_from_dict(d)

        return None

    def get_by_user_id(self, user_id: int) -> Optional[BookingDraft]:
        data = _load_data()

        drafts = _ensure_list(data.get("drafts"))
        for d in reversed(drafts):
            if not isinstance(d, dict):
                continue
            if d.get("user_id") == user_id:
                return _draft_from_dict(d)

        return None


class AppointmentRepository:
    def save_appointment(self, appointment: Appointment):
        data = _load_data()

        items = [a for a in _ensure_list(data.get("appointments")) if isinstance(a, dict)]
        items = [a for a in items if a.get("appointment_id") != appointment.appointment_id]
        items.append(_appointment_to_dict(appointment))

        data["appointments"] = items
        _save_data(data)

    def get_by_draft_id(self, draft_id: str) -> Optional[Appointment]:
        data = _load_data()

        for a in _ensure_list(data.get("appointments")):
            if not isinstance(a, dict):
                continue
            if a.get("draft_id") == draft_id:
                return _appointment_from_dict(a)

        return None

    def get_by_id(self, appointment_id: str) -> Optional[Appointment]:
        data = _load_data()

        for a in _ensure_list(data.get("appointments")):
            if not isinstance(a, dict):
                continue
            if a.get("appointment_id") == appointment_id:
                return _appointment_from_dict(a)

        return None

    def get_by_start_datetime_utc(
        self, start_datetime_utc: str
    ) -> Optional[Appointment]:
        data = _load_data()
        for a in _ensure_list(data.get("appointments")):
            if not isinstance(a, dict):
                continue
            if a.get("start_datetime_utc") == start_datetime_utc:
                return _appointment_from_dict(a)

        return None

    def list_all(self) -> list[Appointment]:
        data = _load_data()
        result: list[Appointment] = []
        for a in _ensure_list(data.get("appointments")):
            ap = _appointment_from_dict(a) if isinstance(a, dict) else None
            if ap is not None:
                result.append(ap)
        return result


class OutboxRepository:
    def save_event(self, event: OutboxEvent):
        data = _load_data()

        items = [e for e in _ensure_list(data.get("outbox")) if isinstance(e, dict)]
        items = [e for e in items if e.get("idempotency_key") != event.idempotency_key]
        items.append(_outbox_to_dict(event))

        data["outbox"] = items
        _save_data(data)

    def get_by_idempotency_key(self, key: str) -> Optional[OutboxEvent]:
        data = _load_data()

        for e in _ensure_list(data.get("outbox")):
            if not isinstance(e, dict):
                continue
            if e.get("idempotency_key") == key:
                return _outbox_from_dict(e)

        return None

    def list_pending(self) -> list[OutboxEvent]:
        data = _load_data()

        events: list[OutboxEvent] = []
        for e in _ensure_list(data.get("outbox")):
            if not isinstance(e, dict):
                continue
            outbox_event = _outbox_from_dict(e)
            if outbox_event is None:
                continue
            if outbox_event.status == OutboxStatus.PENDING:
                events.append(outbox_event)
        return events

    def mark_sent(self, event_id: str):
        data = _load_data()

        for e in _ensure_list(data.get("outbox")):
            if not isinstance(e, dict):
                continue
            if e.get("event_id") == event_id:
                e["status"] = OutboxStatus.SENT.value

        _save_data(data)

    def mark_failed(self, event_id: str, error_message: str):
        data = _load_data()

        for e in _ensure_list(data.get("outbox")):
            if not isinstance(e, dict):
                continue
            if e.get("event_id") == event_id:
                e["status"] = OutboxStatus.FAILED.value
                e["last_error"] = error_message

        _save_data(data)