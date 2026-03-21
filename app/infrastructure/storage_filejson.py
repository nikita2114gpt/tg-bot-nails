import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from app.domain.enums import DraftStep, AppointmentStatus, OutboxType, OutboxStatus
from app.domain.models import BookingDraft, Appointment, OutboxEvent
from app.domain.ops_models import (
    BlacklistEntry,
    ClientLifecycleMarker,
    DayScheduleOverride,
    SalonInfoSettings,
    ScheduleSettings,
    ServiceCatalogItem,
)


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
            return {
                "drafts": [],
                "appointments": [],
                "outbox": [],
                "schedule_settings": {},
                "service_catalog": [],
                "blacklist_entries": [],
                "client_lifecycle_markers": [],
                "day_schedule_overrides": [],
                "salon_info_settings": {},
            }

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
            "schedule_settings": _safe_dict(raw.get("schedule_settings")),
            "service_catalog": _ensure_list(raw.get("service_catalog")),
            "blacklist_entries": _ensure_list(raw.get("blacklist_entries")),
            "client_lifecycle_markers": _ensure_list(raw.get("client_lifecycle_markers")),
            "day_schedule_overrides": _ensure_list(raw.get("day_schedule_overrides")),
            "salon_info_settings": _safe_dict(raw.get("salon_info_settings")),
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


def _schedule_to_dict(value: ScheduleSettings) -> dict:
    return {
        "open_time_hhmm": value.open_time_hhmm,
        "close_time_hhmm": value.close_time_hhmm,
        "slot_minutes": value.slot_minutes,
        "updated_at": value.updated_at,
    }


def _schedule_from_dict(data: dict) -> Optional[ScheduleSettings]:
    if not isinstance(data, dict):
        return None
    try:
        slot_minutes = int(data.get("slot_minutes", 60))
    except Exception:
        slot_minutes = 60
    return ScheduleSettings(
        open_time_hhmm=str(data.get("open_time_hhmm") or "0800"),
        close_time_hhmm=str(data.get("close_time_hhmm") or "2000"),
        slot_minutes=slot_minutes,
        updated_at=data.get("updated_at") if isinstance(data.get("updated_at"), str) else _now_iso(),
    )


def _service_item_to_dict(item: ServiceCatalogItem) -> dict:
    return {
        "service_id": item.service_id,
        "name": item.name,
        "duration_minutes": item.duration_minutes,
        "price_text": item.price_text,
        "is_active": item.is_active,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def _service_item_from_dict(data: dict) -> Optional[ServiceCatalogItem]:
    if not isinstance(data, dict):
        return None
    service_id = data.get("service_id")
    if not isinstance(service_id, str) or not service_id:
        return None
    try:
        duration_minutes = int(data.get("duration_minutes", 60))
    except Exception:
        duration_minutes = 60
    return ServiceCatalogItem(
        service_id=service_id,
        name=str(data.get("name") or ""),
        duration_minutes=duration_minutes,
        price_text=str(data.get("price_text") or "—"),
        is_active=bool(data.get("is_active", True)),
        created_at=data.get("created_at") if isinstance(data.get("created_at"), str) else _now_iso(),
        updated_at=data.get("updated_at") if isinstance(data.get("updated_at"), str) else _now_iso(),
    )


def _blacklist_to_dict(entry: BlacklistEntry) -> dict:
    return {
        "entry_id": entry.entry_id,
        "user_id": entry.user_id,
        "phone_e164": entry.phone_e164,
        "reason": entry.reason,
        "is_active": entry.is_active,
        "created_at": entry.created_at,
        "updated_at": entry.updated_at,
    }


def _blacklist_from_dict(data: dict) -> Optional[BlacklistEntry]:
    if not isinstance(data, dict):
        return None
    entry_id = data.get("entry_id")
    if not isinstance(entry_id, str) or not entry_id:
        return None
    user_id = data.get("user_id")
    if user_id is not None:
        try:
            user_id = int(user_id)
        except Exception:
            user_id = None
    phone_e164 = data.get("phone_e164")
    phone = phone_e164 if isinstance(phone_e164, str) else None
    reason_raw = data.get("reason")
    reason = reason_raw if isinstance(reason_raw, str) else None
    return BlacklistEntry(
        entry_id=entry_id,
        user_id=user_id,
        phone_e164=phone,
        reason=reason,
        is_active=bool(data.get("is_active", True)),
        created_at=data.get("created_at") if isinstance(data.get("created_at"), str) else _now_iso(),
        updated_at=data.get("updated_at") if isinstance(data.get("updated_at"), str) else _now_iso(),
    )


def _marker_to_dict(marker: ClientLifecycleMarker) -> dict:
    return {
        "user_id": marker.user_id,
        "last_confirmed_at": marker.last_confirmed_at,
        "last_confirmed_appointment_id": marker.last_confirmed_appointment_id,
        "last_no_confirm_alert_appointment_id": marker.last_no_confirm_alert_appointment_id,
        "last_reactivation_sent_at": marker.last_reactivation_sent_at,
        "updated_at": marker.updated_at,
    }


def _marker_from_dict(data: dict) -> Optional[ClientLifecycleMarker]:
    if not isinstance(data, dict):
        return None
    try:
        user_id = int(data.get("user_id"))
    except Exception:
        return None
    lca = data.get("last_confirmed_at")
    lca_id = data.get("last_confirmed_appointment_id")
    lnc_id = data.get("last_no_confirm_alert_appointment_id")
    lra = data.get("last_reactivation_sent_at")
    return ClientLifecycleMarker(
        user_id=user_id,
        last_confirmed_at=lca if isinstance(lca, str) else None,
        last_confirmed_appointment_id=lca_id if isinstance(lca_id, str) else None,
        last_no_confirm_alert_appointment_id=lnc_id if isinstance(lnc_id, str) else None,
        last_reactivation_sent_at=lra if isinstance(lra, str) else None,
        updated_at=data.get("updated_at") if isinstance(data.get("updated_at"), str) else _now_iso(),
    )


def _day_override_to_dict(value: DayScheduleOverride) -> dict:
    return {
        "date_yyyymmdd": value.date_yyyymmdd,
        "is_closed": value.is_closed,
        "open_time_hhmm": value.open_time_hhmm,
        "close_time_hhmm": value.close_time_hhmm,
        "slot_minutes": value.slot_minutes,
        "updated_at": value.updated_at,
    }


def _day_override_from_dict(data: dict) -> Optional[DayScheduleOverride]:
    if not isinstance(data, dict):
        return None
    date_yyyymmdd = data.get("date_yyyymmdd")
    if not isinstance(date_yyyymmdd, str) or not date_yyyymmdd:
        return None
    slot_minutes = data.get("slot_minutes")
    if slot_minutes is not None:
        try:
            slot_minutes = int(slot_minutes)
        except Exception:
            slot_minutes = None
    return DayScheduleOverride(
        date_yyyymmdd=date_yyyymmdd,
        is_closed=bool(data.get("is_closed", False)),
        open_time_hhmm=data.get("open_time_hhmm")
        if isinstance(data.get("open_time_hhmm"), str)
        else None,
        close_time_hhmm=data.get("close_time_hhmm")
        if isinstance(data.get("close_time_hhmm"), str)
        else None,
        slot_minutes=slot_minutes,
        updated_at=data.get("updated_at") if isinstance(data.get("updated_at"), str) else _now_iso(),
    )


def _salon_info_to_dict(value: SalonInfoSettings) -> dict:
    return {
        "address_text": value.address_text,
        "contacts_text": value.contacts_text,
        "show_address": value.show_address,
        "show_contacts": value.show_contacts,
        "updated_at": value.updated_at,
    }


def _salon_info_from_dict(data: dict) -> Optional[SalonInfoSettings]:
    if not isinstance(data, dict):
        return None
    return SalonInfoSettings(
        address_text=str(data.get("address_text") or ""),
        contacts_text=str(data.get("contacts_text") or ""),
        show_address=bool(data.get("show_address", True)),
        show_contacts=bool(data.get("show_contacts", True)),
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
            if a.get("start_datetime_utc") != start_datetime_utc:
                continue
            ap = _appointment_from_dict(a)
            if ap is not None and ap.status == AppointmentStatus.CONFIRMED:
                return ap

        return None

    def list_all(self) -> list[Appointment]:
        data = _load_data()
        result: list[Appointment] = []
        for a in _ensure_list(data.get("appointments")):
            ap = _appointment_from_dict(a) if isinstance(a, dict) else None
            if ap is not None:
                result.append(ap)
        return result

    def list_by_user_id(self, user_id: int) -> list[Appointment]:
        data = _load_data()
        result: list[Appointment] = []
        for a in _ensure_list(data.get("appointments")):
            ap = _appointment_from_dict(a) if isinstance(a, dict) else None
            if ap is not None and ap.user_id == user_id:
                result.append(ap)
        result.sort(key=lambda x: x.start_datetime_utc)
        return result

    def get_active_confirmed_for_user(self, user_id: int) -> Optional[Appointment]:
        """
        Активная запись для клиента: CONFIRMED со слотом >= текущего момента (UTC),
        ближайшая по start_datetime_utc; при равенстве слота — appointment_id ASC.
        Только прошлые CONFIRMED не считаются активными.
        """
        now_key = datetime.utcnow().strftime("%Y%m%dT%H%M")
        items = self.list_by_user_id(user_id)
        future = [
            a
            for a in items
            if a.status == AppointmentStatus.CONFIRMED
            and (a.start_datetime_utc or "") >= now_key
        ]
        if not future:
            return None
        future.sort(key=lambda a: (a.start_datetime_utc or "", a.appointment_id or ""))
        return future[0]

    def list_by_status(self, status: AppointmentStatus) -> list[Appointment]:
        data = _load_data()
        result: list[Appointment] = []
        for a in _ensure_list(data.get("appointments")):
            ap = _appointment_from_dict(a) if isinstance(a, dict) else None
            if ap is not None and ap.status == status:
                result.append(ap)
        result.sort(key=lambda x: x.start_datetime_utc)
        return result

    def list_starting_with_date(self, date_yyyymmdd: str) -> list[Appointment]:
        prefix = date_yyyymmdd.strip()
        data = _load_data()
        result: list[Appointment] = []
        for a in _ensure_list(data.get("appointments")):
            ap = _appointment_from_dict(a) if isinstance(a, dict) else None
            if ap is None:
                continue
            sdt = ap.start_datetime_utc or ""
            if sdt.startswith(prefix):
                result.append(ap)
        result.sort(key=lambda x: x.start_datetime_utc)
        return result

    def search_appointments(
        self,
        name_substr: Optional[str] = None,
        phone_substr: Optional[str] = None,
        date_yyyymmdd: Optional[str] = None,
    ) -> list[Appointment]:
        name_substr = (name_substr or "").strip().lower()
        phone_substr = (phone_substr or "").strip().lower()
        date_yyyymmdd = (date_yyyymmdd or "").strip()

        result: list[Appointment] = []
        for ap in self.list_all():
            if name_substr and name_substr not in (ap.customer_name or "").lower():
                continue
            if phone_substr and phone_substr not in (ap.phone_e164 or "").lower():
                continue
            if date_yyyymmdd and not (ap.start_datetime_utc or "").startswith(date_yyyymmdd):
                continue
            result.append(ap)
        result.sort(key=lambda x: x.start_datetime_utc)
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


class ScheduleSettingsRepository:
    def get(self) -> ScheduleSettings:
        data = _load_data()
        parsed = _schedule_from_dict(data.get("schedule_settings"))
        return parsed if parsed is not None else ScheduleSettings()

    def save(self, settings: ScheduleSettings):
        data = _load_data()
        data["schedule_settings"] = _schedule_to_dict(settings)
        _save_data(data)


class DayScheduleOverrideRepository:
    def get_by_date(self, date_yyyymmdd: str) -> Optional[DayScheduleOverride]:
        data = _load_data()
        for row in _ensure_list(data.get("day_schedule_overrides")):
            item = _day_override_from_dict(row) if isinstance(row, dict) else None
            if item is not None and item.date_yyyymmdd == date_yyyymmdd:
                return item
        return None

    def list_all(self) -> list[DayScheduleOverride]:
        data = _load_data()
        result: list[DayScheduleOverride] = []
        for row in _ensure_list(data.get("day_schedule_overrides")):
            item = _day_override_from_dict(row) if isinstance(row, dict) else None
            if item is not None:
                result.append(item)
        result.sort(key=lambda x: x.date_yyyymmdd)
        return result

    def save(self, item: DayScheduleOverride):
        data = _load_data()
        rows = [x for x in _ensure_list(data.get("day_schedule_overrides")) if isinstance(x, dict)]
        rows = [x for x in rows if x.get("date_yyyymmdd") != item.date_yyyymmdd]
        rows.append(_day_override_to_dict(item))
        data["day_schedule_overrides"] = rows
        _save_data(data)


class SalonInfoSettingsRepository:
    def get(self) -> Optional[SalonInfoSettings]:
        data = _load_data()
        return _salon_info_from_dict(data.get("salon_info_settings"))

    def save(self, value: SalonInfoSettings):
        data = _load_data()
        data["salon_info_settings"] = _salon_info_to_dict(value)
        _save_data(data)


class ServiceCatalogRepository:
    def save_item(self, item: ServiceCatalogItem):
        data = _load_data()
        rows = [x for x in _ensure_list(data.get("service_catalog")) if isinstance(x, dict)]
        rows = [x for x in rows if x.get("service_id") != item.service_id]
        rows.append(_service_item_to_dict(item))
        data["service_catalog"] = rows
        _save_data(data)

    def get_by_id(self, service_id: str) -> Optional[ServiceCatalogItem]:
        data = _load_data()
        for row in _ensure_list(data.get("service_catalog")):
            if not isinstance(row, dict):
                continue
            if row.get("service_id") == service_id:
                return _service_item_from_dict(row)
        return None

    def list_all(self) -> list[ServiceCatalogItem]:
        data = _load_data()
        result: list[ServiceCatalogItem] = []
        for row in _ensure_list(data.get("service_catalog")):
            item = _service_item_from_dict(row) if isinstance(row, dict) else None
            if item is not None:
                result.append(item)
        result.sort(key=lambda x: (not x.is_active, x.name.lower()))
        return result

    def delete_item(self, service_id: str):
        data = _load_data()
        rows = [x for x in _ensure_list(data.get("service_catalog")) if isinstance(x, dict)]
        rows = [x for x in rows if x.get("service_id") != service_id]
        data["service_catalog"] = rows
        _save_data(data)


class BlacklistRepository:
    def save_entry(self, entry: BlacklistEntry):
        data = _load_data()
        rows = [x for x in _ensure_list(data.get("blacklist_entries")) if isinstance(x, dict)]
        rows = [x for x in rows if x.get("entry_id") != entry.entry_id]
        rows.append(_blacklist_to_dict(entry))
        data["blacklist_entries"] = rows
        _save_data(data)

    def get_active_by_user_id(self, user_id: int) -> Optional[BlacklistEntry]:
        data = _load_data()
        for row in reversed(_ensure_list(data.get("blacklist_entries"))):
            entry = _blacklist_from_dict(row) if isinstance(row, dict) else None
            if entry is None:
                continue
            if entry.is_active and entry.user_id == user_id:
                return entry
        return None

    def get_active_by_phone(self, phone_e164: str) -> Optional[BlacklistEntry]:
        data = _load_data()
        for row in reversed(_ensure_list(data.get("blacklist_entries"))):
            entry = _blacklist_from_dict(row) if isinstance(row, dict) else None
            if entry is None:
                continue
            if entry.is_active and entry.phone_e164 == phone_e164:
                return entry
        return None

    def list_all(self) -> list[BlacklistEntry]:
        data = _load_data()
        result: list[BlacklistEntry] = []
        for row in _ensure_list(data.get("blacklist_entries")):
            entry = _blacklist_from_dict(row) if isinstance(row, dict) else None
            if entry is not None:
                result.append(entry)
        result.sort(key=lambda x: (not x.is_active, x.created_at), reverse=False)
        return result


class ClientLifecycleMarkerRepository:
    def get_by_user_id(self, user_id: int) -> Optional[ClientLifecycleMarker]:
        data = _load_data()
        for row in _ensure_list(data.get("client_lifecycle_markers")):
            marker = _marker_from_dict(row) if isinstance(row, dict) else None
            if marker is not None and marker.user_id == user_id:
                return marker
        return None

    def save(self, marker: ClientLifecycleMarker):
        data = _load_data()
        rows = [
            x
            for x in _ensure_list(data.get("client_lifecycle_markers"))
            if isinstance(x, dict)
        ]
        rows = [x for x in rows if x.get("user_id") != marker.user_id]
        rows.append(_marker_to_dict(marker))
        data["client_lifecycle_markers"] = rows
        _save_data(data)