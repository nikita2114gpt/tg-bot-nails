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
from app.application.service_catalog_view import resolve_service_price_and_duration_optional
from app.domain.enums import AppointmentStatus, DraftStep, OutboxType
from app.domain.models import Appointment, BookingDraft, OutboxEvent

# Одна «активная» будущая CONFIRMED на пользователя; см. get_active_confirmed_for_user.
ACTIVE_BOOKING_CONFLICT_MESSAGE = (
    "У вас уже есть активная запись.\n"
    "Проверьте ее в разделе \"Моя запись\" или отмените ее перед новой записью."
)
BLACKLIST_CONFLICT_MESSAGE = "Запись недоступна. Обратитесь к администратору."
# ConflictError при коллизии слота в confirm_booking (presentation сравнивает с этим текстом).
BOOKING_SLOT_CONFLICT_MESSAGE = "Это время уже занято, выберите другое."


class BookingUseCases:
    def __init__(
        self,
        draft_repo,
        appointment_repo,
        outbox_repo,
        allowed_services: list[str],
        service_catalog_repo=None,
        blacklist_repo=None,
        lifecycle_repo=None,
        allow_multiple_active_bookings: bool = False,
    ):
        self.draft_repo = draft_repo
        self.appointment_repo = appointment_repo
        self.outbox_repo = outbox_repo
        self.allowed_services = allowed_services
        self.service_catalog_repo = service_catalog_repo
        self.blacklist_repo = blacklist_repo
        self.lifecycle_repo = lifecycle_repo
        self._allow_multiple_active_bookings = allow_multiple_active_bookings

    def list_available_services(self) -> list[str]:
        # Runtime canonical rule (temporary, explicit):
        # service_catalog.name is used as the service value written to draft.service_id
        # and appointment.service_id. No mapping layer is introduced at this stage.
        repo = self.service_catalog_repo
        if repo is None:
            return list(self.allowed_services)
        try:
            rows = repo.list_all()
            names = [x.name.strip() for x in rows if getattr(x, "is_active", False) and x.name.strip()]
            if names:
                # Keep stable order and avoid duplicate labels from catalog.
                return list(dict.fromkeys(names))
        except Exception:
            pass
        return list(self.allowed_services)

    def _ensure_not_blacklisted(self, user_id: int, phone_e164: str | None = None) -> None:
        repo = self.blacklist_repo
        if repo is None:
            return
        try:
            if repo.get_active_by_user_id(user_id) is not None:
                raise ConflictError(BLACKLIST_CONFLICT_MESSAGE)
            if phone_e164 and repo.get_active_by_phone(phone_e164) is not None:
                raise ConflictError(BLACKLIST_CONFLICT_MESSAGE)
        except ConflictError:
            raise
        except Exception:
            # Не ломаем runtime при временных проблемах data-layer.
            return

    def _get_active_confirmed_for_user(self, user_id: int) -> Appointment | None:
        fn = getattr(self.appointment_repo, "get_active_confirmed_for_user", None)
        if callable(fn):
            return fn(user_id)
        try:
            items = self.appointment_repo.list_by_user_id(user_id)
        except AttributeError:
            items = [a for a in self.appointment_repo.list_all() if a.user_id == user_id]
        now_key = datetime.utcnow().strftime("%Y%m%dT%H%M")
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

    def _get_active_confirmed_for_phone(
        self,
        phone_e164: str,
        *,
        exclude_draft_id: str | None = None,
    ) -> Appointment | None:
        """Будущая CONFIRMED с тем же телефоном (для лимита одной активной записи на номер)."""
        if not (phone_e164 or "").strip():
            return None
        norm = (phone_e164 or "").strip()
        now_key = datetime.utcnow().strftime("%Y%m%dT%H%M")
        try:
            items = self.appointment_repo.list_all()
        except Exception:
            return None
        candidates: list[Appointment] = []
        for a in items:
            if a.status != AppointmentStatus.CONFIRMED:
                continue
            if (a.phone_e164 or "").strip() != norm:
                continue
            if (a.start_datetime_utc or "") < now_key:
                continue
            if exclude_draft_id and (a.draft_id or "") == exclude_draft_id:
                continue
            candidates.append(a)
        if not candidates:
            return None
        candidates.sort(key=lambda x: (x.start_datetime_utc or "", x.appointment_id or ""))
        return candidates[0]

    def start_booking(self, user_id: int) -> BookingDraft:
        self._ensure_not_blacklisted(user_id)
        if (
            not self._allow_multiple_active_bookings
            and self._get_active_confirmed_for_user(user_id) is not None
        ):
            raise ConflictError(ACTIVE_BOOKING_CONFLICT_MESSAGE)
        draft = BookingDraft(
            user_id=user_id,
            step=DraftStep.CHOOSE_SERVICE,
        )
        self.draft_repo.save_draft(draft)
        fn = getattr(self.draft_repo, "cancel_other_drafts_for_user", None)
        if callable(fn):
            try:
                fn(user_id, draft.draft_id)
            except Exception:
                pass
        return draft

    def start_booking_for_operator(self, operator_user_id: int) -> BookingDraft:
        """
        Черновик записи от имени админа: не блокируем оператора по его собственной активной записи;
        лимит «одна активная на номер» проверяется в confirm по телефону клиента.
        """
        self._ensure_not_blacklisted(operator_user_id)
        draft = BookingDraft(
            user_id=operator_user_id,
            step=DraftStep.CHOOSE_SERVICE,
        )
        self.draft_repo.save_draft(draft)
        fn = getattr(self.draft_repo, "cancel_other_drafts_for_user", None)
        if callable(fn):
            try:
                fn(operator_user_id, draft.draft_id)
            except Exception:
                pass
        return draft

    def choose_service(self, draft_id: str, user_id: int, service_id: str) -> BookingDraft:
        draft = self._get_user_draft(draft_id, user_id)

        allowed = self.list_available_services()
        if not validate_service_id(service_id, allowed):
            raise UserInputError("Некорректная услуга")

        # Двойной клик / два параллельных callback: оба читают CHOOSE_SERVICE, первый уже
        # перевёл черновик в CHOOSE_DATE — второй должен пройти без ошибки при той же услуге.
        if draft.step == DraftStep.CHOOSE_DATE:
            if (draft.service_id or "").strip() == (service_id or "").strip():
                return draft
            raise UserInputError("Неверный шаг для выбора услуги")

        if draft.step != DraftStep.CHOOSE_SERVICE:
            raise UserInputError("Неверный шаг для выбора услуги")

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
        now_key = datetime.now().strftime("%Y%m%dT%H%M")
        if start_datetime_utc <= now_key:
            raise UserInputError("Нельзя выбрать прошедшее время")

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
        self._ensure_not_blacklisted(user_id, normalized_phone)

        updated = replace(
            draft,
            customer_name=name.strip(),
            phone_e164=normalized_phone,
            step=DraftStep.CONFIRM,
            updated_at=self._now_iso(),
        )
        self.draft_repo.save_draft(updated)
        return updated

    def remember_client_phone_for_lifecycle(self, user_id: int, phone_e164: str | None) -> None:
        """Сохраняет номер для клиента (только доверенные вызовы из client UI)."""
        if self.lifecycle_repo is None:
            return
        p = (phone_e164 or "").strip()
        if not p:
            return
        try:
            from app.domain.ops_models import ClientLifecycleMarker

            marker = self.lifecycle_repo.get_by_user_id(int(user_id))
            if marker is None:
                marker = ClientLifecycleMarker(user_id=int(user_id))
            marker.last_phone_e164 = p
            marker.updated_at = self._now_iso()
            self.lifecycle_repo.save(marker)
        except Exception:
            pass

    def remember_client_phone_from_draft(self, user_id: int, draft_id: str) -> None:
        draft = self.draft_repo.get_by_id(draft_id)
        if draft is None or int(draft.user_id) != int(user_id):
            return
        self.remember_client_phone_for_lifecycle(user_id, draft.phone_e164)

    def get_active_confirmed_for_phone(
        self, phone_e164: str, *, exclude_draft_id: str | None = None
    ) -> Appointment | None:
        return self._get_active_confirmed_for_phone(
            phone_e164, exclude_draft_id=exclude_draft_id
        )

    def confirm_booking(
        self,
        draft_id: str,
        user_id: int,
        *,
        walk_in_client: bool = False,
    ) -> Appointment:
        draft = self._get_user_draft(draft_id, user_id)

        # Идемпотентность: запись уже создана по этому draft_id — не гоняем слот/insert повторно.
        existing_ap = self.appointment_repo.get_by_draft_id(draft_id)
        if existing_ap is not None and existing_ap.status == AppointmentStatus.CONFIRMED:
            if draft.step != DraftStep.CONFIRM_DONE:
                fixed = replace(draft, step=DraftStep.CONFIRM_DONE, updated_at=self._now_iso())
                self.draft_repo.save_draft(fixed)
            return existing_ap

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
        self._ensure_not_blacklisted(user_id, draft.phone_e164)

        ap_draft = self.appointment_repo.get_by_draft_id(draft.draft_id)
        already_confirmed_here = (
            ap_draft is not None and ap_draft.status == AppointmentStatus.CONFIRMED
        )
        if not self._allow_multiple_active_bookings and not already_confirmed_here:
            if not walk_in_client:
                active_other = self._get_active_confirmed_for_user(user_id)
                if active_other is not None and (
                    ap_draft is None or active_other.appointment_id != ap_draft.appointment_id
                ):
                    raise ConflictError(ACTIVE_BOOKING_CONFLICT_MESSAGE)
            active_phone = self._get_active_confirmed_for_phone(
                draft.phone_e164 or "",
                exclude_draft_id=draft.draft_id,
            )
            if active_phone is not None and (
                ap_draft is None or active_phone.appointment_id != ap_draft.appointment_id
            ):
                raise ConflictError(ACTIVE_BOOKING_CONFLICT_MESSAGE)

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
            and existing_for_slot.status == AppointmentStatus.CONFIRMED
            and existing_for_slot.draft_id != draft.draft_id
        ):
            raise ConflictError(BOOKING_SLOT_CONFLICT_MESSAGE)

        try:
            appointment = create_appointment_once(
                draft_id=draft.draft_id,
                get_existing_fn=self.appointment_repo.get_by_draft_id,
                create_fn=lambda: self._create_appointment_from_draft(
                    draft,
                    walk_in_client=walk_in_client,
                ),
            )
        except Exception as e:
            # SQLite race-protection: unique slot constraint can still fail
            # if two confirmations happen concurrently.
            try:
                import sqlite3

                if isinstance(e, sqlite3.IntegrityError) and "start_datetime_utc" in str(e):
                    raise ConflictError(BOOKING_SLOT_CONFLICT_MESSAGE)
            except Exception:
                pass

            if "UNIQUE constraint failed" in str(e) and "start_datetime_utc" in str(e):
                raise ConflictError(BOOKING_SLOT_CONFLICT_MESSAGE)

            raise

        self._create_admin_notify_event_once(appointment)
        if appointment.user_id:
            self._create_reminder_event_once(appointment, hours_before=24)
            self._create_reminder_event_once(appointment, hours_before=2)

        updated = replace(
            draft,
            step=DraftStep.CONFIRM_DONE,
            updated_at=self._now_iso(),
        )
        self.draft_repo.save_draft(updated)

        # Marker for future lifecycle communication (reactivation).
        if self.lifecycle_repo is not None and appointment.user_id:
            try:
                from app.domain.ops_models import ClientLifecycleMarker

                marker = self.lifecycle_repo.get_by_user_id(int(appointment.user_id))
                if marker is None:
                    marker = ClientLifecycleMarker(user_id=int(appointment.user_id))
                marker.last_confirmed_at = self._now_iso()
                marker.last_confirmed_appointment_id = appointment.appointment_id
                marker.updated_at = self._now_iso()
                self.lifecycle_repo.save(marker)
            except Exception:
                pass

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

    def _create_appointment_from_draft(
        self,
        draft: BookingDraft,
        *,
        walk_in_client: bool = False,
    ) -> Appointment:
        uid = 0 if walk_in_client else draft.user_id
        appointment = Appointment(
            draft_id=draft.draft_id,
            user_id=uid,
            service_id=draft.service_id or "",
            start_datetime_utc=draft.start_datetime_utc or "",
            customer_name=draft.customer_name or "",
            phone_e164=draft.phone_e164 or "",
        )
        self.appointment_repo.save_appointment(appointment)
        return appointment

    def _create_admin_notify_event_once(self, appointment: Appointment) -> OutboxEvent:
        key = f"admin_notify:{appointment.appointment_id}"
        service_price_text, service_duration_text = resolve_service_price_and_duration_optional(
            self.service_catalog_repo,
            appointment.service_id,
        )

        return create_outbox_once(
            idempotency_key=key,
            get_existing_fn=self.outbox_repo.get_by_idempotency_key,
            create_fn=lambda: self._save_outbox_event(
                OutboxEvent(
                    event_type=OutboxType.ADMIN_NOTIFY,
                    idempotency_key=key,
                    payload={
                        "event_kind": "new_booking",
                        "appointment_id": appointment.appointment_id,
                        "draft_id": appointment.draft_id,
                        "user_id": appointment.user_id,
                        "service_id": appointment.service_id,
                        "service_price_text": service_price_text,
                        "service_duration_text": service_duration_text,
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