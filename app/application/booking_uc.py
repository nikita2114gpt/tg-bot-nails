from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from app.core.errors import ConflictError, NotFoundError, UserInputError
from app.application.idempotency import (
    create_appointment_once,
    create_outbox_once,
)
from app.application.validation import (
    normalize_phone,
    validate_date_string,
    validate_name,
    validate_not_past_date,
    validate_phone,
    validate_service_id,
    validate_time_string,
)
from app.domain.enums import DraftStep, OutboxType
from app.domain.models import Appointment, BookingDraft, OutboxEvent


class BookingUseCases:
    def __init__(
        self,
        draft_repo,
        appointment_repo,
        outbox_repo,
        allowed_services: list[str],
    ):
        self.draft_repo = draft_repo
        self.appointment_repo = appointment_repo
        self.outbox_repo = outbox_repo
        self.allowed_services = allowed_services

    def start_booking(self, user_id: int) -> BookingDraft:
        draft = BookingDraft(
            user_id=user_id,
            step=DraftStep.CHOOSE_SERVICE,
        )
        self.draft_repo.save_draft(draft)
        return draft

    def choose_service(self, draft_id: str, user_id: int, service_id: str) -> BookingDraft:
        draft = self._get_user_draft(draft_id, user_id)

        if draft.step != DraftStep.CHOOSE_SERVICE:
            raise UserInputError("Неверный шаг для выбора услуги")

        if not validate_service_id(service_id, self.allowed_services):
            raise UserInputError("Некорректная услуга")

        updated = replace(
            draft,
            service_id=service_id,
            step=DraftStep.CHOOSE_DATE,
            updated_at=self._now_iso(),
        )
        self.draft_repo.save_draft(updated)
        return updated

    def choose_date(self, draft_id: str, user_id: int, date_value: str) -> BookingDraft:
        draft = self._get_user_draft(draft_id, user_id)

        if draft.step != DraftStep.CHOOSE_DATE:
            raise UserInputError("Неверный шаг для выбора даты")

        if not validate_date_string(date_value):
            raise UserInputError("Некорректный формат даты")

        if not validate_not_past_date(date_value):
            raise UserInputError("Нельзя выбрать прошедшую дату")

        updated = replace(
            draft,
            appointment_date=date_value,
            step=DraftStep.CHOOSE_TIME,
            updated_at=self._now_iso(),
        )
        self.draft_repo.save_draft(updated)
        return updated

    def choose_time(self, draft_id: str, user_id: int, time_value: str) -> BookingDraft:
        draft = self._get_user_draft(draft_id, user_id)

        if draft.step != DraftStep.CHOOSE_TIME:
            raise UserInputError("Неверный шаг для выбора времени")

        if not validate_time_string(time_value):
            raise UserInputError("Некорректный формат времени")

        if not draft.appointment_date:
            raise UserInputError("Сначала нужно выбрать дату")

        start_datetime_utc = f"{draft.appointment_date}T{time_value}"

        updated = replace(
            draft,
            appointment_time=time_value,
            start_datetime_utc=start_datetime_utc,
            step=DraftStep.ENTER_CONTACT,
            updated_at=self._now_iso(),
        )
        self.draft_repo.save_draft(updated)
        return updated

    def enter_contact(
        self,
        draft_id: str,
        user_id: int,
        name: str,
        phone: str,
    ) -> BookingDraft:
        draft = self._get_user_draft(draft_id, user_id)

        if draft.step != DraftStep.ENTER_CONTACT:
            raise UserInputError("Неверный шаг для ввода контакта")

        if not validate_name(name):
            raise UserInputError("Некорректное имя")

        if not validate_phone(phone):
            raise UserInputError("Некорректный телефон")

        normalized_phone = normalize_phone(phone)

        updated = replace(
            draft,
            customer_name=name.strip(),
            phone_e164=normalized_phone,
            step=DraftStep.CONFIRM,
            updated_at=self._now_iso(),
        )
        self.draft_repo.save_draft(updated)
        return updated

    def confirm_booking(self, draft_id: str, user_id: int) -> Appointment:
        draft = self._get_user_draft(draft_id, user_id)

        if draft.step != DraftStep.CONFIRM:
            raise UserInputError("Неверный шаг для подтверждения")

        if not all(
            [
                draft.service_id,
                draft.appointment_date,
                draft.appointment_time,
                draft.start_datetime_utc,
                draft.customer_name,
                draft.phone_e164,
            ]
        ):
            raise ConflictError("Черновик заполнен не полностью. Попробуйте начать заново: /start")

        # Slot uniqueness: two different drafts must not occupy the same start_datetime_utc.
        existing_for_slot = None
        try:
            existing_for_slot = self.appointment_repo.get_by_start_datetime_utc(
                draft.start_datetime_utc
            )
        except AttributeError:
            # Backward compatibility for older repository implementations.
            existing_for_slot = None

        if (
            existing_for_slot is not None
            and existing_for_slot.draft_id != draft.draft_id
        ):
            raise ConflictError("Это время уже занято, выберите другое.")

        try:
            appointment = create_appointment_once(
                draft_id=draft.draft_id,
                get_existing_fn=self.appointment_repo.get_by_draft_id,
                create_fn=lambda: self._create_appointment_from_draft(draft),
            )
        except Exception as e:
            # SQLite race-protection: unique slot constraint can still fail
            # if two confirmations happen concurrently.
            try:
                import sqlite3

                if isinstance(e, sqlite3.IntegrityError) and "start_datetime_utc" in str(e):
                    raise ConflictError("Это время уже занято, выберите другое.")
            except Exception:
                pass

            if "UNIQUE constraint failed" in str(e) and "start_datetime_utc" in str(e):
                raise ConflictError("Это время уже занято, выберите другое.")

            raise

        self._create_admin_notify_event_once(appointment)
        self._create_reminder_event_once(appointment, hours_before=24)
        self._create_reminder_event_once(appointment, hours_before=1)

        updated = replace(
            draft,
            step=DraftStep.CONFIRM_DONE,
            updated_at=self._now_iso(),
        )
        self.draft_repo.save_draft(updated)

        return appointment

    def cancel_booking(self, draft_id: str, user_id: int) -> BookingDraft:
        draft = self._get_user_draft(draft_id, user_id)

        updated = replace(
            draft,
            step=DraftStep.CANCELLED,
            updated_at=self._now_iso(),
        )
        self.draft_repo.save_draft(updated)
        return updated

    def back_booking(self, draft_id: str, user_id: int) -> "BackResult":
        """
        Выполняет бизнес-переход назад по шагам сценария.

        Presentation layer отвечает только за отображение (FSM state + UI действия),
        а источник истины переходов - step в хранилище.
        """

        draft = self.draft_repo.get_by_id(draft_id)
        if draft is None or draft.user_id != user_id or draft.step == DraftStep.CANCELLED:
            return BackResult(kind="stale")

        if draft.step == DraftStep.CHOOSE_DATE:
            draft.step = DraftStep.CHOOSE_SERVICE
            draft.updated_at = self._now_iso()
            self.draft_repo.save_draft(draft)
            return BackResult(kind="choose_service")

        if draft.step == DraftStep.CHOOSE_TIME:
            draft.step = DraftStep.CHOOSE_DATE
            draft.updated_at = self._now_iso()
            self.draft_repo.save_draft(draft)
            return BackResult(kind="choose_date")

        # UX/Telegram specifics:
        # After choosing time the presentation sends contact message (ReplyKeyboardMarkup),
        # but the old inline message with the "back" button may remain visible.
        # If user presses that stale "Назад" while draft.step already moved to ENTER_CONTACT,
        # we still want to go back to date selection.
        if draft.step == DraftStep.ENTER_CONTACT:
            draft.step = DraftStep.CHOOSE_DATE
            draft.updated_at = self._now_iso()
            self.draft_repo.save_draft(draft)
            return BackResult(kind="choose_date")

        if draft.step == DraftStep.CONFIRM:
            draft.step = DraftStep.ENTER_CONTACT
            draft.updated_at = self._now_iso()
            self.draft_repo.save_draft(draft)
            return BackResult(kind="enter_contact")

        return BackResult(kind="not_available")

    def _get_user_draft(self, draft_id: str, user_id: int) -> BookingDraft:
        draft = self.draft_repo.get_by_id(draft_id)

        if draft is None:
            raise NotFoundError()

        if draft.user_id != user_id:
            raise NotFoundError()

        if draft.step == DraftStep.CANCELLED:
            raise NotFoundError("Сессия устарела. Начните заново: /start")

        return draft

    def _create_appointment_from_draft(self, draft: BookingDraft) -> Appointment:
        appointment = Appointment(
            draft_id=draft.draft_id,
            user_id=draft.user_id,
            service_id=draft.service_id or "",
            start_datetime_utc=draft.start_datetime_utc or "",
            customer_name=draft.customer_name or "",
            phone_e164=draft.phone_e164 or "",
        )
        self.appointment_repo.save_appointment(appointment)
        return appointment

    def _create_admin_notify_event_once(self, appointment: Appointment) -> OutboxEvent:
        key = f"admin_notify:{appointment.appointment_id}"

        return create_outbox_once(
            idempotency_key=key,
            get_existing_fn=self.outbox_repo.get_by_idempotency_key,
            create_fn=lambda: self._save_outbox_event(
                OutboxEvent(
                    event_type=OutboxType.ADMIN_NOTIFY,
                    idempotency_key=key,
                    payload={
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

    def _create_reminder_event_once(self, appointment: Appointment, hours_before: int) -> OutboxEvent:
        key = f"reminder:{appointment.appointment_id}:{hours_before}"
        send_at = self._build_reminder_send_at(appointment.start_datetime_utc, hours_before)

        return create_outbox_once(
            idempotency_key=key,
            get_existing_fn=self.outbox_repo.get_by_idempotency_key,
            create_fn=lambda: self._save_outbox_event(
                OutboxEvent(
                    event_type=OutboxType.REMINDER_CLIENT,
                    idempotency_key=key,
                    send_at=send_at,
                    payload={
                        "appointment_id": appointment.appointment_id,
                        "user_id": appointment.user_id,
                        "service_id": appointment.service_id,
                        "start_datetime_utc": appointment.start_datetime_utc,
                        "customer_name": appointment.customer_name,
                        "phone_e164": appointment.phone_e164,
                        "hours_before": hours_before,
                    },
                )
            ),
        )

    def _save_outbox_event(self, event: OutboxEvent) -> OutboxEvent:
        self.outbox_repo.save_event(event)
        return event

    def _build_reminder_send_at(self, start_datetime_utc: str, hours_before: int) -> str:
        dt = self._parse_start_datetime(start_datetime_utc)
        reminder_dt = dt - timedelta(hours=hours_before)
        return reminder_dt.isoformat()

    def _parse_start_datetime(self, value: str) -> datetime:
        return datetime.strptime(value, "%Y%m%dT%H%M")

    def _now_iso(self) -> str:
        return datetime.utcnow().isoformat()


@dataclass(frozen=True)
class BackResult:
    # kind:
    # - "stale": сессия устарела / draft не найден / draft cancelled
    # - "choose_service": шаг CHOOSE_SERVICE
    # - "choose_date": шаг CHOOSE_DATE
    # - "enter_contact": шаг ENTER_CONTACT
    # - "not_available": back недоступен на данном шаге
    kind: str