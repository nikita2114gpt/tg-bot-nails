from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import re
from typing import Optional

from app.core.errors import ConflictError, NotFoundError, UserInputError
from app.domain.ops_models import (
    BlacklistEntry,
    DayScheduleOverride,
    DaySlotOverride,
    PriceListItem,
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
        raise UserInputError("Ожидается время в формате HH:MM (минуты только :00 или :30).")
    hh = int(raw[:2])
    mm = int(raw[2:])
    if hh < 0 or hh > 23 or mm < 0 or mm > 59:
        raise UserInputError("Некорректное время.")
    if mm % 30 != 0:
        raise UserInputError("Минуты времени должны быть :00 или :30 (кратно 30 мин).")
    return f"{hh:02d}{mm:02d}"


def _assert_slot_minutes_step30(n: int) -> None:
    if n <= 0 or n > 240:
        raise UserInputError("Шаг слотов должен быть от 1 до 240 минут и кратен 30 минутам.")
    if n % 30 != 0:
        raise UserInputError("Шаг слотов должен быть кратен 30 минутам.")


def _hhmm_to_minutes(hhmm: str) -> int:
    return int(hhmm[:2]) * 60 + int(hhmm[2:])


class AdminOpsUseCases:
    def __init__(
        self,
        schedule_repo,
        service_repo,
        blacklist_repo,
        day_schedule_repo=None,
        day_slot_override_repo=None,
        salon_info_repo=None,
        price_list_repo=None,
    ) -> None:
        self.schedule_repo = schedule_repo
        self.service_repo = service_repo
        self.blacklist_repo = blacklist_repo
        self.day_schedule_repo = day_schedule_repo
        self.day_slot_override_repo = day_slot_override_repo
        self.salon_info_repo = salon_info_repo
        self.price_list_repo = price_list_repo

    def list_price_items(self) -> list[PriceListItem]:
        if self.price_list_repo is None:
            return []
        return self.price_list_repo.list_all()

    def list_active_price_items(self) -> list[PriceListItem]:
        if self.price_list_repo is None:
            return []
        return self.price_list_repo.list_active()

    def get_price_item_or_raise(self, item_id: str) -> PriceListItem:
        if self.price_list_repo is None:
            raise ConflictError("Раздел прайса недоступен.")
        item = self.price_list_repo.get_by_id(item_id)
        if item is None:
            raise NotFoundError("Позиция прайса не найдена.")
        return item

    def parse_price_display_text(self, raw: str) -> str:
        text = (raw or "").strip()
        if not text:
            raise UserInputError(
                "Некорректный формат. Используйте: Название, цена."
            )
        if len(text) > 2000:
            raise UserInputError("Текст слишком длинный.")

        pattern = (
            r"^\s*(?P<title>[^,]+?)\s*,\s*"
            r"(?P<price>\d[\d\s]*(?:[.,]\d+)?\s*(?:₽|руб\.?|р\.?)?)"
            r"\s*$"
        )
        m = re.match(pattern, text, flags=re.IGNORECASE)
        if m is None:
            raise UserInputError(
                "Некорректный формат. Используйте: Название, цена.\n"
                "Пример: Маникюр, 2000"
            )

        title = re.sub(r"\s+", " ", (m.group("title") or "").strip())
        price = re.sub(r"\s+", " ", (m.group("price") or "").strip())
        if not title:
            raise UserInputError("Название обязательно.")
        if not price:
            raise UserInputError("Цена обязательна.")

        return "\n".join([title, f"Цена: {price}"])

    def add_price_item(self, raw: str) -> PriceListItem:
        if self.price_list_repo is None:
            raise ConflictError("Раздел прайса недоступен.")
        display = self.parse_price_display_text(raw)
        item = PriceListItem(display_text=display, is_active=True)
        self.price_list_repo.save(item)
        return item

    def update_price_item_text(self, item_id: str, raw: str) -> PriceListItem:
        if self.price_list_repo is None:
            raise ConflictError("Раздел прайса недоступен.")
        current = self.get_price_item_or_raise(item_id)
        display = self.parse_price_display_text(raw)
        updated = replace(
            current,
            display_text=display,
            updated_at=_now_iso(),
        )
        self.price_list_repo.save(updated)
        return updated

    def deactivate_price_item(self, item_id: str) -> PriceListItem:
        if self.price_list_repo is None:
            raise ConflictError("Раздел прайса недоступен.")
        current = self.get_price_item_or_raise(item_id)
        updated = replace(current, is_active=False, updated_at=_now_iso())
        self.price_list_repo.save(updated)
        return updated

    def activate_price_item(self, item_id: str) -> PriceListItem:
        if self.price_list_repo is None:
            raise ConflictError("Раздел прайса недоступен.")
        current = self.get_price_item_or_raise(item_id)
        updated = replace(current, is_active=True, updated_at=_now_iso())
        self.price_list_repo.save(updated)
        return updated

    def delete_price_item(self, item_id: str) -> None:
        if self.price_list_repo is None:
            raise ConflictError("Раздел прайса недоступен.")
        self.get_price_item_or_raise(item_id)
        self.price_list_repo.delete(item_id)

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
                workday_end_time_hhmm=base.workday_end_time_hhmm or base.close_time_hhmm,
                slot_minutes=base.slot_minutes,
                updated_at=base.updated_at,
            )
        override = self.get_day_override(date_yyyymmdd)
        if override is None:
            return base
        return ScheduleSettings(
            open_time_hhmm=override.open_time_hhmm or base.open_time_hhmm,
            close_time_hhmm=override.close_time_hhmm or base.close_time_hhmm,
            workday_end_time_hhmm=(
                override.workday_end_time_hhmm
                or base.workday_end_time_hhmm
                or base.close_time_hhmm
            ),
            slot_minutes=override.slot_minutes or base.slot_minutes,
            updated_at=override.updated_at,
        )

    def get_effective_workday_end_for_date(self, date_yyyymmdd: str) -> str:
        sched = self.get_effective_schedule_for_date(date_yyyymmdd)
        return sched.workday_end_time_hhmm or sched.close_time_hhmm

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
        workday_end_time_hhmm: Optional[str] = None,
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
        new_workday_end = (
            _parse_hhmm(workday_end_time_hhmm)
            if workday_end_time_hhmm is not None
            else (
                current.workday_end_time_hhmm
                or self.get_schedule().workday_end_time_hhmm
                or self.get_schedule().close_time_hhmm
            )
        )
        new_step = (
            int(slot_minutes)
            if slot_minutes is not None
            else (current.slot_minutes or self.get_schedule().slot_minutes)
        )
        if slot_minutes is not None:
            _assert_slot_minutes_step30(new_step)
        if _hhmm_to_minutes(new_close) <= _hhmm_to_minutes(new_open):
            raise ConflictError("Конец дня должен быть позже начала дня.")
        if _hhmm_to_minutes(new_close) > _hhmm_to_minutes(new_workday_end):
            raise ConflictError(
                "Последний слот не может быть позже конца рабочего дня. "
                "Сначала увеличьте конец рабочего дня."
            )

        updated = replace(
            current,
            is_closed=False,
            open_time_hhmm=new_open,
            close_time_hhmm=new_close,
            workday_end_time_hhmm=new_workday_end,
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
        workday_end_time_hhmm: Optional[str] = None,
        slot_minutes: Optional[int] = None,
        reset_day_workday_end_overrides: bool = False,
    ) -> ScheduleSettings:
        current = self.schedule_repo.get()
        open_hhmm = _parse_hhmm(open_time_hhmm) if open_time_hhmm is not None else current.open_time_hhmm
        close_hhmm = _parse_hhmm(close_time_hhmm) if close_time_hhmm is not None else current.close_time_hhmm
        workday_end_hhmm = (
            _parse_hhmm(workday_end_time_hhmm)
            if workday_end_time_hhmm is not None
            else (current.workday_end_time_hhmm or current.close_time_hhmm)
        )
        new_step = int(slot_minutes) if slot_minutes is not None else current.slot_minutes
        if slot_minutes is not None:
            _assert_slot_minutes_step30(new_step)
        if _hhmm_to_minutes(close_hhmm) <= _hhmm_to_minutes(open_hhmm):
            raise ConflictError("Конец дня должен быть позже начала дня.")
        if _hhmm_to_minutes(close_hhmm) > _hhmm_to_minutes(workday_end_hhmm):
            raise ConflictError(
                "Последний слот не может быть позже конца рабочего дня. "
                "Сначала увеличьте конец рабочего дня."
            )
        updated = ScheduleSettings(
            open_time_hhmm=open_hhmm,
            close_time_hhmm=close_hhmm,
            workday_end_time_hhmm=workday_end_hhmm,
            slot_minutes=new_step,
            updated_at=_now_iso(),
        )
        self.schedule_repo.save(updated)
        if open_time_hhmm is not None:
            self._clear_day_override_open_times()
        if close_time_hhmm is not None:
            self._clear_day_override_close_times()
        if workday_end_time_hhmm is not None and reset_day_workday_end_overrides:
            self._clear_day_override_workday_end_times()
        return updated

    def _clear_day_override_open_times(self) -> None:
        if self.day_schedule_repo is None:
            return
        list_fn = getattr(self.day_schedule_repo, "list_all", None)
        if not callable(list_fn):
            return
        for row in list_fn():
            if row.open_time_hhmm is None:
                continue
            cleared = replace(row, open_time_hhmm=None, updated_at=_now_iso())
            self.day_schedule_repo.save(cleared)

    def _clear_day_override_close_times(self) -> None:
        if self.day_schedule_repo is None:
            return
        list_fn = getattr(self.day_schedule_repo, "list_all", None)
        if not callable(list_fn):
            return
        for row in list_fn():
            if row.close_time_hhmm is None:
                continue
            cleared = replace(row, close_time_hhmm=None, updated_at=_now_iso())
            self.day_schedule_repo.save(cleared)

    def _clear_day_override_workday_end_times(self) -> None:
        if self.day_schedule_repo is None:
            return
        list_fn = getattr(self.day_schedule_repo, "list_all", None)
        if not callable(list_fn):
            return
        for row in list_fn():
            if row.workday_end_time_hhmm is None:
                continue
            cleared = replace(row, workday_end_time_hhmm=None, updated_at=_now_iso())
            self.day_schedule_repo.save(cleared)

    def update_schedule_slot_unify_day_overrides(self, slot_minutes: int) -> ScheduleSettings:
        """Сохраняет глобальный шаг слотов и сбрасывает индивидуальные slot_minutes у day override."""
        updated = self.update_schedule(slot_minutes=slot_minutes)
        if self.day_schedule_repo is None:
            return updated
        list_fn = getattr(self.day_schedule_repo, "list_all", None)
        if not callable(list_fn):
            return updated
        for row in list_fn():
            if row.slot_minutes is None:
                continue
            cleared = replace(row, slot_minutes=None, updated_at=_now_iso())
            self.day_schedule_repo.save(cleared)
        return updated

    def get_day_slot_override(self, date_yyyymmdd: str, slot_hhmm: str) -> Optional[DaySlotOverride]:
        if self.day_slot_override_repo is None:
            return None
        return self.day_slot_override_repo.get(date_yyyymmdd, slot_hhmm)

    def is_slot_disabled(self, date_yyyymmdd: str, slot_hhmm: str) -> bool:
        row = self.get_day_slot_override(date_yyyymmdd, slot_hhmm)
        return bool(row.is_disabled) if row is not None else False

    def day_has_disabled_slot(self, date_yyyymmdd: str) -> bool:
        repo = self.day_slot_override_repo
        if repo is None:
            return False
        list_fn = getattr(repo, "list_by_date", None)
        if not callable(list_fn):
            return False
        try:
            for row in list_fn(date_yyyymmdd):
                if bool(getattr(row, "is_disabled", False)):
                    return True
        except Exception:
            return False
        return False

    def is_service_interval_blocked_by_disabled_slots(
        self,
        date_yyyymmdd: str,
        start_hhmm: str,
        duration_minutes: int,
    ) -> bool:
        repo = self.day_slot_override_repo
        if repo is None:
            return False
        list_fn = getattr(repo, "list_by_date", None)
        if not callable(list_fn):
            return False
        raw = (start_hhmm or "").strip()
        if len(raw) != 4 or not raw.isdigit():
            return False
        new_start = int(raw[:2]) * 60 + int(raw[2:])
        try:
            dur = max(int(duration_minutes), 5)
        except (TypeError, ValueError):
            dur = 60
        new_end = new_start + dur
        for row in list_fn(date_yyyymmdd):
            if not row.is_disabled:
                continue
            sh = (row.slot_hhmm or "").strip()
            if len(sh) != 4 or not sh.isdigit():
                continue
            d_min = int(sh[:2]) * 60 + int(sh[2:])
            if new_start <= d_min < new_end:
                return True
        return False

    def set_slot_disabled(self, date_yyyymmdd: str, slot_hhmm: str, is_disabled: bool) -> DaySlotOverride:
        if self.day_slot_override_repo is None:
            raise ConflictError("Настройки слотов недоступны.")
        current = self.day_slot_override_repo.get(date_yyyymmdd, slot_hhmm)
        if current is None:
            current = DaySlotOverride(date_yyyymmdd=date_yyyymmdd, slot_hhmm=slot_hhmm)
        updated = replace(
            current,
            is_disabled=bool(is_disabled),
            updated_at=_now_iso(),
        )
        self.day_slot_override_repo.save(updated)
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
        clean_price = (price_text or "").strip()
        if not clean_name:
            raise UserInputError("Название услуги не может быть пустым.")
        if not clean_price or clean_price == "—":
            raise UserInputError("Цена услуги обязательна.")
        if duration_minutes <= 0 or duration_minutes > 24 * 60:
            raise UserInputError("Некорректная длительность услуги.")
        if duration_minutes % 30 != 0:
            raise UserInputError("Длительность должна быть кратна 30 минутам.")
        item = ServiceCatalogItem(
            name=clean_name,
            duration_minutes=duration_minutes,
            price_text=clean_price,
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
