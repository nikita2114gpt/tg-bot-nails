from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Optional

from app.core.errors import ConflictError, NotFoundError, UserInputError
from app.domain.ops_models import (
    BlacklistEntry,
    DayScheduleOverride,
    SalonInfoSettings,
    ScheduleSettings,
    ServiceCatalogItem,
)

DEFAULT_CLOSED_WEEKDAYS = (5, 6)  # Saturday, Sunday


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


def _parse_hhmm(value: str) -> str:
    raw = (value or "").strip().replace(":", "")
    if len(raw) != 4 or not raw.isdigit():
        raise UserInputError("Ожидается время в формате HH:MM.")
    hh = int(raw[:2])
    mm = int(raw[2:])
    if hh < 0 or hh > 23 or mm < 0 or mm > 59:
        raise UserInputError("Некорректное время.")
    return f"{hh:02d}{mm:02d}"


def _hhmm_to_minutes(hhmm: str) -> int:
    return int(hhmm[:2]) * 60 + int(hhmm[2:])


class AdminOpsUseCases:
    def __init__(
        self,
        schedule_repo,
        service_repo,
        blacklist_repo,
        day_schedule_repo=None,
        salon_info_repo=None,
    ) -> None:
        self.schedule_repo = schedule_repo
        self.service_repo = service_repo
        self.blacklist_repo = blacklist_repo
        self.day_schedule_repo = day_schedule_repo
        self.salon_info_repo = salon_info_repo

    def get_schedule(self) -> ScheduleSettings:
        return self.schedule_repo.get()

    def get_day_override(self, date_yyyymmdd: str) -> Optional[DayScheduleOverride]:
        if self.day_schedule_repo is None:
            return None
        return self.day_schedule_repo.get_by_date(date_yyyymmdd)

    def is_day_closed(self, date_yyyymmdd: str) -> bool:
        override = self.get_day_override(date_yyyymmdd)
        if override is not None:
            return bool(override.is_closed)
        # Default weekends are closed: Sat(5), Sun(6)
        try:
            dt = datetime.strptime(date_yyyymmdd, "%Y%m%d")
            return dt.weekday() in DEFAULT_CLOSED_WEEKDAYS
        except Exception:
            return False

    def get_effective_schedule_for_date(self, date_yyyymmdd: str) -> ScheduleSettings:
        base = self.get_schedule()
        if self.is_day_closed(date_yyyymmdd):
            return ScheduleSettings(
                open_time_hhmm=base.open_time_hhmm,
                close_time_hhmm=base.close_time_hhmm,
                slot_minutes=base.slot_minutes,
                updated_at=base.updated_at,
            )
        override = self.get_day_override(date_yyyymmdd)
        if override is None:
            return base
        return ScheduleSettings(
            open_time_hhmm=override.open_time_hhmm or base.open_time_hhmm,
            close_time_hhmm=override.close_time_hhmm or base.close_time_hhmm,
            slot_minutes=override.slot_minutes or base.slot_minutes,
            updated_at=override.updated_at,
        )

    def set_day_closed(self, date_yyyymmdd: str, is_closed: bool) -> DayScheduleOverride:
        if self.day_schedule_repo is None:
            raise ConflictError("Day overrides недоступны.")
        current = self.day_schedule_repo.get_by_date(date_yyyymmdd)
        if current is None:
            current = DayScheduleOverride(date_yyyymmdd=date_yyyymmdd)
        updated = replace(current, is_closed=bool(is_closed), updated_at=_now_iso())
        self.day_schedule_repo.save(updated)
        return updated

    def update_day_schedule(
        self,
        date_yyyymmdd: str,
        *,
        open_time_hhmm: Optional[str] = None,
        close_time_hhmm: Optional[str] = None,
        slot_minutes: Optional[int] = None,
    ) -> DayScheduleOverride:
        if self.day_schedule_repo is None:
            raise ConflictError("Day overrides недоступны.")
        current = self.day_schedule_repo.get_by_date(date_yyyymmdd)
        if current is None:
            current = DayScheduleOverride(date_yyyymmdd=date_yyyymmdd)

        new_open = (
            _parse_hhmm(open_time_hhmm)
            if open_time_hhmm is not None
            else (current.open_time_hhmm or self.get_schedule().open_time_hhmm)
        )
        new_close = (
            _parse_hhmm(close_time_hhmm)
            if close_time_hhmm is not None
            else (current.close_time_hhmm or self.get_schedule().close_time_hhmm)
        )
        new_step = (
            int(slot_minutes)
            if slot_minutes is not None
            else (current.slot_minutes or self.get_schedule().slot_minutes)
        )
        if new_step <= 0 or new_step > 240:
            raise UserInputError("Некорректный шаг слотов.")
        if _hhmm_to_minutes(new_close) <= _hhmm_to_minutes(new_open):
            raise ConflictError("Конец дня должен быть позже начала дня.")

        updated = replace(
            current,
            is_closed=False,
            open_time_hhmm=new_open,
            close_time_hhmm=new_close,
            slot_minutes=new_step,
            updated_at=_now_iso(),
        )
        self.day_schedule_repo.save(updated)
        return updated

    def update_schedule(
        self,
        *,
        open_time_hhmm: Optional[str] = None,
        close_time_hhmm: Optional[str] = None,
        slot_minutes: Optional[int] = None,
    ) -> ScheduleSettings:
        current = self.schedule_repo.get()
        open_hhmm = _parse_hhmm(open_time_hhmm) if open_time_hhmm is not None else current.open_time_hhmm
        close_hhmm = _parse_hhmm(close_time_hhmm) if close_time_hhmm is not None else current.close_time_hhmm
        new_step = int(slot_minutes) if slot_minutes is not None else current.slot_minutes
        if new_step <= 0:
            raise UserInputError("Шаг слотов должен быть положительным.")
        if new_step > 240:
            raise UserInputError("Шаг слотов слишком большой.")
        if _hhmm_to_minutes(close_hhmm) <= _hhmm_to_minutes(open_hhmm):
            raise ConflictError("Конец дня должен быть позже начала дня.")
        updated = ScheduleSettings(
            open_time_hhmm=open_hhmm,
            close_time_hhmm=close_hhmm,
            slot_minutes=new_step,
            updated_at=_now_iso(),
        )
        self.schedule_repo.save(updated)
        return updated

    def list_services(self) -> list[ServiceCatalogItem]:
        return self.service_repo.list_all()

    def list_active_services(self) -> list[ServiceCatalogItem]:
        return [x for x in self.service_repo.list_all() if x.is_active and x.name.strip()]

    def get_service_slot_step_minutes(self, service_name: str) -> int:
        for item in self.service_repo.list_all():
            if item.is_active and item.name.strip() == (service_name or "").strip():
                if isinstance(item.duration_minutes, int) and item.duration_minutes > 0:
                    return item.duration_minutes
        return self.get_schedule().slot_minutes

    def add_service(self, name: str, duration_minutes: int, price_text: str) -> ServiceCatalogItem:
        clean_name = (name or "").strip()
        if not clean_name:
            raise UserInputError("Название услуги не может быть пустым.")
        if duration_minutes <= 0 or duration_minutes > 24 * 60:
            raise UserInputError("Некорректная длительность услуги.")
        if duration_minutes % 30 != 0:
            raise UserInputError("Длительность должна быть кратна 30 минутам.")
        item = ServiceCatalogItem(
            name=clean_name,
            duration_minutes=duration_minutes,
            price_text=(price_text or "—").strip() or "—",
            is_active=True,
        )
        self.service_repo.save_item(item)
        return item

    def get_service_or_raise(self, service_id: str) -> ServiceCatalogItem:
        item = self.service_repo.get_by_id(service_id)
        if item is None:
            raise NotFoundError("Услуга не найдена.")
        return item

    def update_service(
        self,
        service_id: str,
        *,
        name: Optional[str] = None,
        duration_minutes: Optional[int] = None,
        price_text: Optional[str] = None,
        is_active: Optional[bool] = None,
    ) -> ServiceCatalogItem:
        current = self.get_service_or_raise(service_id)
        new_name = (name.strip() if name is not None else current.name)
        if not new_name:
            raise UserInputError("Название услуги не может быть пустым.")
        new_duration = current.duration_minutes
        if duration_minutes is not None:
            new_duration = int(duration_minutes)
            if new_duration <= 0 or new_duration > 24 * 60:
                raise UserInputError("Некорректная длительность услуги.")
            if new_duration % 30 != 0:
                raise UserInputError("Длительность должна быть кратна 30 минутам.")
        new_price = (price_text.strip() if price_text is not None else current.price_text) or "—"
        updated = replace(
            current,
            name=new_name,
            duration_minutes=new_duration,
            price_text=new_price,
            is_active=current.is_active if is_active is None else bool(is_active),
            updated_at=_now_iso(),
        )
        self.service_repo.save_item(updated)
        return updated

    def delete_service(self, service_id: str) -> None:
        self.get_service_or_raise(service_id)
        fn = getattr(self.service_repo, "delete_item", None)
        if callable(fn):
            fn(service_id)
            return
        # Fallback for older repos: hide from active catalog.
        self.update_service(service_id, is_active=False)

    def list_blacklist(self) -> list[BlacklistEntry]:
        return self.blacklist_repo.list_all()

    def add_blacklist_entry(
        self,
        *,
        user_id: Optional[int] = None,
        phone_e164: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> BlacklistEntry:
        if user_id is None and not phone_e164:
            raise UserInputError("Укажите user_id или телефон.")
        normalized_phone = (phone_e164 or "").strip() or None
        entry = BlacklistEntry(
            user_id=user_id,
            phone_e164=normalized_phone,
            reason=(reason or "").strip() or None,
            is_active=True,
        )
        self.blacklist_repo.save_entry(entry)
        return entry

    def deactivate_blacklist_entry(self, entry_id: str) -> BlacklistEntry:
        rows = self.blacklist_repo.list_all()
        for row in rows:
            if row.entry_id == entry_id:
                if not row.is_active:
                    return row
                updated = replace(row, is_active=False, updated_at=_now_iso())
                self.blacklist_repo.save_entry(updated)
                return updated
        raise NotFoundError("Запись blacklist не найдена.")

    def get_salon_info(self, default_address: str, default_contacts: str) -> SalonInfoSettings:
        if self.salon_info_repo is None:
            return SalonInfoSettings(
                address_text=default_address,
                contacts_text=default_contacts,
            )
        current = self.salon_info_repo.get()
        if current is None:
            current = SalonInfoSettings(
                address_text=default_address,
                contacts_text=default_contacts,
            )
            self.salon_info_repo.save(current)
        if not current.address_text:
            current.address_text = default_address
        if not current.contacts_text:
            current.contacts_text = default_contacts
        return current

    def update_salon_info(
        self,
        *,
        default_address: str,
        default_contacts: str,
        address_text: Optional[str] = None,
        contacts_text: Optional[str] = None,
        show_address: Optional[bool] = None,
        show_contacts: Optional[bool] = None,
    ) -> SalonInfoSettings:
        if self.salon_info_repo is None:
            raise ConflictError("Настройки салона недоступны.")
        current = self.get_salon_info(default_address, default_contacts)
        updated = replace(
            current,
            address_text=current.address_text if address_text is None else (address_text.strip() or ""),
            contacts_text=current.contacts_text if contacts_text is None else (contacts_text.strip() or ""),
            show_address=current.show_address if show_address is None else bool(show_address),
            show_contacts=current.show_contacts if show_contacts is None else bool(show_contacts),
            updated_at=_now_iso(),
        )
        self.salon_info_repo.save(updated)
        return updated
