from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Optional

from app.application.validation import (
    normalize_phone,
    validate_date_string,
    validate_name,
    validate_not_past_date,
    validate_phone,
    validate_service_id,
    validate_time_string,
)
from app.application.idempotency import create_outbox_once
from app.core.errors import ConflictError, NotFoundError, UserInputError
from app.domain.enums import AppointmentStatus, OutboxType
from app.domain.models import Appointment, OutboxEvent


def format_slot_utc_for_user(value: str) -> str:
    if not value or "T" not in value:
        return value or "—"
    date_part, time_part = value.split("T", 1)
    if len(date_part) >= 8 and len(time_part) >= 4:
        y, m, d = date_part[:4], date_part[4:6], date_part[6:8]
        return f"{d}.{m}.{y} {time_part[:2]}:{time_part[2:4]}"
    return value


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


class AppointmentUseCases:
    def __init__(
        self,
        appointment_repo: object,
        allowed_services: list[str],
        outbox_repo: object | None = None,
    ) -> None:
        self.appointment_repo = appointment_repo
        self.allowed_services = allowed_services
        self.outbox_repo = outbox_repo

    def _emit_admin_event_once(
        self,
        *,
        kind: str,
        appointment: Appointment,
        dedupe_suffix: str = "",
    ) -> None:
        if self.outbox_repo is None:
            return
        key = f"admin_event:{kind}:{appointment.appointment_id}"
        if dedupe_suffix:
            key = f"{key}:{dedupe_suffix}"

        create_outbox_once(
            idempotency_key=key,
            get_existing_fn=self.outbox_repo.get_by_idempotency_key,
            create_fn=lambda: self._save_outbox_event(
                OutboxEvent(
                    event_type=OutboxType.ADMIN_NOTIFY,
                    idempotency_key=key,
                    payload={
                        "event_kind": kind,
                        "appointment_id": appointment.appointment_id,
                        "draft_id": appointment.draft_id,
                        "user_id": appointment.user_id,
                        "service_id": appointment.service_id,
                        "start_datetime_utc": appointment.start_datetime_utc,
                        "customer_name": appointment.customer_name,
                        "phone_e164": appointment.phone_e164,
                    },
                )
            ),
        )

    def _save_outbox_event(self, event: OutboxEvent) -> OutboxEvent:
        if self.outbox_repo is not None:
            self.outbox_repo.save_event(event)
        return event

    def _list_by_user(self, user_id: int) -> list[Appointment]:
        repo = self.appointment_repo
        fn = getattr(repo, "list_by_user_id", None)
        if callable(fn):
            return list(fn(user_id))
        items = [a for a in repo.list_all() if a.user_id == user_id]
        items.sort(key=lambda x: x.start_datetime_utc)
        return items

    def _list_starting_with_date(self, ymd: str) -> list[Appointment]:
        repo = self.appointment_repo
        fn = getattr(repo, "list_starting_with_date", None)
        if callable(fn):
            return list(fn(ymd))
        prefix = ymd.strip()
        items = [
            a
            for a in repo.list_all()
            if (a.start_datetime_utc or "").startswith(prefix)
        ]
        items.sort(key=lambda x: x.start_datetime_utc)
        return items

    def _list_by_status(self, status: AppointmentStatus) -> list[Appointment]:
        repo = self.appointment_repo
        fn = getattr(repo, "list_by_status", None)
        if callable(fn):
            return list(fn(status))
        items = [a for a in repo.list_all() if a.status == status]
        items.sort(key=lambda x: x.start_datetime_utc)
        return items

    def _search(
        self,
        name_substr: Optional[str],
        phone_substr: Optional[str],
        date_yyyymmdd: Optional[str],
    ) -> list[Appointment]:
        repo = self.appointment_repo
        fn = getattr(repo, "search_appointments", None)
        if callable(fn):
            return list(fn(name_substr, phone_substr, date_yyyymmdd))
        name_substr = (name_substr or "").strip().lower()
        phone_substr = (phone_substr or "").strip().lower()
        date_yyyymmdd = (date_yyyymmdd or "").strip()
        result: list[Appointment] = []
        for ap in repo.list_all():
            if name_substr and name_substr not in (ap.customer_name or "").lower():
                continue
            if phone_substr and phone_substr not in (ap.phone_e164 or "").lower():
                continue
            if date_yyyymmdd and not (ap.start_datetime_utc or "").startswith(date_yyyymmdd):
                continue
            result.append(ap)
        result.sort(key=lambda x: x.start_datetime_utc)
        return result

    def get_my_active_appointment(self, user_id: int) -> Optional[Appointment]:
        """
        Активная запись для self-service: как get_active_confirmed_for_user —
        только будущие (slot >= now UTC) CONFIRMED, ближайшая по слоту.
        """
        fn = getattr(self.appointment_repo, "get_active_confirmed_for_user", None)
        if callable(fn):
            return fn(user_id)
        now_key = datetime.utcnow().strftime("%Y%m%dT%H%M")
        items = self._list_by_user(user_id)
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

    def _cancel_appointment_for_user(
        self, ap: Appointment, user_id: int
    ) -> tuple[Appointment, bool]:
        if ap.user_id != user_id:
            raise NotFoundError("Запись не найдена.")
        if ap.status == AppointmentStatus.CANCELLED:
            return ap, True
        updated = replace(ap, status=AppointmentStatus.CANCELLED, updated_at=_now_iso())
        self.appointment_repo.save_appointment(updated)
        self._emit_admin_event_once(kind="client_cancelled", appointment=updated)
        return updated, False

    def cancel_my_appointment(self, user_id: int) -> tuple[Appointment, bool]:
        ap = self.get_my_active_appointment(user_id)
        if ap is None:
            raise NotFoundError("Активных записей нет.")
        return self._cancel_appointment_for_user(ap, user_id)

    def cancel_my_appointment_by_id(
        self, user_id: int, appointment_id: str
    ) -> tuple[Appointment, bool]:
        canonical = self.get_my_active_appointment(user_id)
        if canonical is None or canonical.appointment_id != appointment_id:
            raise NotFoundError(
                "Эта запись не является вашей текущей активной записью. Откройте «Моя запись»."
            )
        return self._cancel_appointment_for_user(canonical, user_id)

    def admin_list_today(self) -> list[Appointment]:
        ymd = datetime.utcnow().strftime("%Y%m%d")
        return self._list_starting_with_date(ymd)

    def admin_list_for_date(self, date_yyyymmdd: str) -> list[Appointment]:
        if not validate_date_string(date_yyyymmdd):
            raise UserInputError("Некорректная дата.")
        return self._list_starting_with_date(date_yyyymmdd)

    def admin_list_cancelled(self) -> list[Appointment]:
        return self._list_by_status(AppointmentStatus.CANCELLED)

    def admin_search(self, raw_query: str) -> list[Appointment]:
        q = (raw_query or "").strip()
        if not q:
            return []
        if len(q) == 8 and q.isdigit():
            return self._search(None, None, q)
        compact = q.replace(" ", "")
        if compact.startswith("+") or compact[:1].isdigit():
            if sum(ch.isdigit() for ch in compact) >= 5:
                return self._search(None, q, None)
        return self._search(q, None, None)

    def get_appointment_or_raise(self, appointment_id: str) -> Appointment:
        ap = self.appointment_repo.get_by_id(appointment_id)
        if ap is None:
            raise NotFoundError("Запись не найдена.")
        return ap

    def admin_cancel(self, appointment_id: str) -> tuple[Appointment, bool]:
        ap = self.get_appointment_or_raise(appointment_id)
        if ap.status == AppointmentStatus.CANCELLED:
            return ap, True
        updated = replace(ap, status=AppointmentStatus.CANCELLED, updated_at=_now_iso())
        self.appointment_repo.save_appointment(updated)
        self._emit_admin_event_once(kind="admin_cancelled", appointment=updated)
        return updated, False

    def admin_update(
        self,
        appointment_id: str,
        *,
        service_id: Optional[str] = None,
        start_datetime_utc: Optional[str] = None,
        customer_name: Optional[str] = None,
        phone_e164: Optional[str] = None,
    ) -> Appointment:
        ap = self.get_appointment_or_raise(appointment_id)
        new_service = service_id if service_id is not None else ap.service_id
        new_start = start_datetime_utc if start_datetime_utc is not None else ap.start_datetime_utc
        new_name = customer_name if customer_name is not None else ap.customer_name
        new_phone = phone_e164 if phone_e164 is not None else ap.phone_e164

        if service_id is not None:
            if not validate_service_id(new_service, self.allowed_services):
                raise UserInputError("Некорректная услуга.")

        if start_datetime_utc is not None:
            if "T" not in new_start or len(new_start) < 13:
                raise UserInputError("Некорректный формат даты/времени.")
            d_part, t_part = new_start.split("T", 1)
            if not validate_date_string(d_part) or not validate_time_string(t_part):
                raise UserInputError("Некорректная дата или время.")
            if not validate_not_past_date(d_part):
                raise UserInputError("Нельзя выбрать прошедшую дату.")

        if customer_name is not None:
            if not validate_name(new_name):
                raise UserInputError("Некорректное имя.")

        if phone_e164 is not None:
            if not validate_phone(new_phone):
                raise UserInputError("Некорректный телефон.")
            new_phone = normalize_phone(new_phone)

        if new_start != ap.start_datetime_utc:
            other = self.appointment_repo.get_by_start_datetime_utc(new_start)
            if other is not None and other.appointment_id != ap.appointment_id:
                raise ConflictError("Это время уже занято.")

        updated = replace(
            ap,
            service_id=new_service,
            start_datetime_utc=new_start,
            customer_name=new_name,
            phone_e164=new_phone,
            updated_at=_now_iso(),
        )
        try:
            self.appointment_repo.save_appointment(updated)
        except Exception as e:
            try:
                import sqlite3

                if isinstance(e, sqlite3.IntegrityError) and "start_datetime_utc" in str(e):
                    raise ConflictError("Это время уже занято.") from e
            except ConflictError:
                raise
            except Exception:
                pass
            if "UNIQUE constraint failed" in str(e) and "start_datetime_utc" in str(e):
                raise ConflictError("Это время уже занято.") from e
            raise
        self._emit_admin_event_once(
            kind="appointment_edited",
            appointment=updated,
            dedupe_suffix=updated.updated_at,
        )
        return updated
