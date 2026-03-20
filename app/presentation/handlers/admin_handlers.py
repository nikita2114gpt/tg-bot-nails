from __future__ import annotations

from datetime import date, timedelta

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.application.admin_ops_uc import AdminOpsUseCases
from app.application.appointment_uc import AppointmentUseCases, format_slot_utc_for_user
from app.config import Settings
from app.core.errors import AppError, error_to_user_message
from app.domain.enums import AppointmentStatus
from app.domain.models import Appointment
from app.domain.ops_models import BlacklistEntry, ScheduleSettings, ServiceCatalogItem
from app.presentation.callback.nav_callbacks import (
    build_admin_cancel_appt,
    build_admin_edit_menu,
    build_admin_edit_name,
    build_admin_edit_phone,
    build_admin_home,
    build_admin_month,
    build_admin_month_date,
    build_admin_open,
    build_admin_blacklist_from_appointment,
    build_admin_cancelled_page,
    build_admin_records,
    build_admin_search,
    build_admin_set_date,
    build_admin_set_service,
    build_admin_set_time,
    build_admin_time_slot,
    build_admin_cancelled,
    build_admin_ops_blacklist,
    build_admin_ops_blacklist_add,
    build_admin_ops_blacklist_deactivate,
    build_admin_day_toggle,
    build_admin_day_edit,
    build_admin_move_start,
    build_admin_move_date,
    build_admin_move_time,
    build_admin_ops_salon,
    build_admin_ops_salon_edit,
    build_admin_ops_salon_toggle,
    build_admin_ops_schedule,
    build_admin_ops_schedule_edit,
    build_admin_ops_service_delete,
    build_admin_ops_service_add,
    build_admin_ops_service_deactivate,
    build_admin_ops_service_edit,
    build_admin_ops_service_open,
    build_admin_ops_service_toggle,
    build_admin_ops_services,
    parse_admin,
)
from app.presentation.fsm.states import AdminStates

router = Router(name="admin")


def _is_admin(user_id: int | None, settings: Settings) -> bool:
    return user_id is not None and user_id in set(settings.admin_ids)


def _month_keyboard(month_shift: int = 0) -> InlineKeyboardBuilder:
    today = date.today()
    start = today + timedelta(days=month_shift * 30)
    if start < today:
        start = today
        month_shift = 0

    b = InlineKeyboardBuilder()
    if month_shift > 0:
        b.button(text="«", callback_data=build_admin_month(month_shift - 1))
    else:
        b.button(text="·", callback_data="a1|noop")
    end = start + timedelta(days=29)
    b.button(
        text=f"{start.strftime('%d.%m')} - {end.strftime('%d.%m')}",
        callback_data="a1|noop",
    )
    b.button(text="»", callback_data=build_admin_month(month_shift + 1))

    for i in range(30):
        d = start + timedelta(days=i)
        ymd = d.strftime("%Y%m%d")
        b.button(text=d.strftime("%d.%m"), callback_data=build_admin_month_date(ymd))
    b.button(text="« Меню", callback_data=build_admin_home())
    b.adjust(3, 5, 5, 5, 5, 5, 5, 1)
    return b


def _admin_dates(settings: Settings, count: int = 8) -> list[str]:
    today = date.today()
    max_days = max(int(settings.max_days_ahead), 0)
    if max_days <= 0:
        return [today.strftime("%Y%m%d")]
    days = min(count, max_days + 1)
    return [(today + timedelta(days=i)).strftime("%Y%m%d") for i in range(days)]


def _admin_time_slots() -> list[str]:
    slots: list[str] = []
    for hour in range(8, 21):
        slots.append(f"{hour:02d}00")
    return slots


def _slots_for_schedule(open_hhmm: str, close_hhmm: str, step: int) -> list[str]:
    try:
        start = int(open_hhmm[:2]) * 60 + int(open_hhmm[2:])
        end = int(close_hhmm[:2]) * 60 + int(close_hhmm[2:])
    except Exception:
        return _admin_time_slots()
    if step <= 0:
        step = 60
    if end < start:
        end = start
    slots: list[str] = []
    cur = start
    while cur <= end and len(slots) < 200:
        hh = (cur // 60) % 24
        mm = cur % 60
        slots.append(f"{hh:02d}{mm:02d}")
        cur += step
    return slots or _admin_time_slots()


def _home_keyboard() -> InlineKeyboardBuilder:
    b = InlineKeyboardBuilder()
    b.button(text="Записи", callback_data=build_admin_records())
    b.button(text="Отменённые", callback_data=build_admin_cancelled())
    b.button(text="Поиск", callback_data=build_admin_search())
    b.button(text="Расписание", callback_data=build_admin_ops_schedule())
    b.button(text="Адрес / Контакты", callback_data=build_admin_ops_salon())
    b.button(text="Услуги", callback_data=build_admin_ops_services())
    b.button(text="Blacklist", callback_data=build_admin_ops_blacklist())
    b.adjust(1, 2, 2, 2)
    return b


def _fmt_hhmm(hhmm: str) -> str:
    if len(hhmm) == 4 and hhmm.isdigit():
        return f"{hhmm[:2]}:{hhmm[2:]}"
    return hhmm


def _fmt_duration_minutes(value: int) -> str:
    h = value // 60
    m = value % 60
    if h > 0 and m > 0:
        return f"{h} ч {m} мин"
    if h > 0:
        return f"{h} ч"
    return f"{m} мин"


def _hhmm_to_minutes(hhmm: str) -> int | None:
    if len(hhmm) != 4 or not hhmm.isdigit():
        return None
    return int(hhmm[:2]) * 60 + int(hhmm[2:])


def _slot_is_occupied_for_items(
    items: list[Appointment],
    admin_ops_uc: AdminOpsUseCases,
    hhmm: str,
    exclude_appointment_id: str | None = None,
) -> Appointment | None:
    slot_min = _hhmm_to_minutes(hhmm)
    if slot_min is None:
        return None
    for ap in items:
        if ap.status == AppointmentStatus.CANCELLED:
            continue
        if exclude_appointment_id and ap.appointment_id == exclude_appointment_id:
            continue
        s = ap.start_datetime_utc or ""
        if len(s) < 13:
            continue
        start_hhmm = s[9:13]
        start_min = _hhmm_to_minutes(start_hhmm)
        if start_min is None:
            continue
        try:
            duration_min = max(int(admin_ops_uc.get_service_slot_step_minutes(ap.service_id)), 5)
        except Exception:
            duration_min = 60
        end_min = start_min + duration_min
        if start_min <= slot_min < end_min:
            return ap
    return None


def _schedule_text(value: ScheduleSettings) -> str:
    return (
        "<b>Общее расписание</b>\n\n"
        f"Начало дня: {_fmt_hhmm(value.open_time_hhmm)}\n"
        f"Конец дня: {_fmt_hhmm(value.close_time_hhmm)}\n"
        f"Шаг слотов: {_fmt_duration_minutes(value.slot_minutes)}"
    )


def _service_line(item: ServiceCatalogItem) -> str:
    mark = "🟢" if item.is_active else "⚪"
    dur = _fmt_duration_minutes(item.duration_minutes)
    return f"{mark} {item.name} · {dur} · {item.price_text}"


def _blacklist_line(entry: BlacklistEntry) -> str:
    mark = "🟢" if entry.is_active else "⚪"
    who = f"user_id={entry.user_id}" if entry.user_id is not None else f"tel={entry.phone_e164 or '—'}"
    return f"{mark} {who}"


def _btn_line(ap: Appointment) -> str:
    sdt = ap.start_datetime_utc or ""
    hm = "—"
    if "T" in sdt and len(sdt) >= 13:
        t = sdt.split("T", 1)[1]
        if len(t) >= 4:
            hm = f"{t[:2]}:{t[2:4]}"
    name = (ap.customer_name or "")[:18]
    return f"{hm} · {name}"


def _card_text(ap: Appointment, settings: Settings) -> str:
    when = format_slot_utc_for_user(ap.start_datetime_utc)
    phone = ap.phone_e164 or "—"
    st = ap.status.value
    return (
        f"<b>Запись</b> ({st})\n\n"
        f"🕐 {when}\n"
        f"💇 {ap.service_id}\n"
        f"👤 {ap.customer_name}\n"
        f"📞 {phone}"
    )


def _fmt_ymd(ymd: str) -> str:
    if len(ymd) == 8 and ymd.isdigit():
        return f"{ymd[6:8]}.{ymd[4:6]}.{ymd[0:4]}"
    return ymd


async def _safe_edit_or_answer(callback: CallbackQuery, text: str, reply_markup=None) -> None:
    if callback.message is None:
        return
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except Exception:
        await callback.message.answer(text, reply_markup=reply_markup)


def _menu_only_kb():
    b = InlineKeyboardBuilder()
    b.button(text="« Меню", callback_data=build_admin_home())
    return b.as_markup()


async def _safe_cq_answer(callback: CallbackQuery) -> None:
    try:
        await callback.answer(cache_time=0)
    except Exception:
        pass


def _split_answer(text: str, limit: int = 3800) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    buf: list[str] = []
    n = 0
    for line in text.split("\n"):
        if n + len(line) + 1 > limit and buf:
            parts.append("\n".join(buf))
            buf = []
            n = 0
        buf.append(line)
        n += len(line) + 1
    if buf:
        parts.append("\n".join(buf))
    return parts


@router.message(Command("admin"))
async def admin_entry(message: Message, settings: Settings) -> None:
    uid = message.from_user.id if message.from_user else None
    if not _is_admin(uid, settings):
        await message.answer("Недостаточно прав.")
        return
    await message.answer(
        "Админ: записи",
        reply_markup=_home_keyboard().as_markup(),
    )


@router.callback_query(F.data.startswith("a1"))
async def admin_callback(
    callback: CallbackQuery,
    state: FSMContext,
    settings: Settings,
    appointment_uc: AppointmentUseCases,
    admin_ops_uc: AdminOpsUseCases,
) -> None:
    await _safe_cq_answer(callback)
    if callback.message is None:
        return
    uid = callback.from_user.id if callback.from_user else None
    if not _is_admin(uid, settings):
        await callback.message.answer("Недостаточно прав.")
        return

    try:
        action, parts = parse_admin(callback.data or "")
    except ValueError:
        await callback.message.answer("Команда устарела. Откройте /admin заново.")
        return

    await state.clear()

    if action == "home":
        await callback.message.answer(
            "Админ: записи",
            reply_markup=_home_keyboard().as_markup(),
        )
        return

    if action == "noop":
        await callback.answer("Выберите дату в календаре", show_alert=False)
        return

    if action == "td":
        await callback.message.answer(
            "Календарь записей:",
            reply_markup=_month_keyboard(0).as_markup(),
        )
        return

    if action == "cal":
        await callback.message.answer(
            "Календарь записей:",
            reply_markup=_month_keyboard(0).as_markup(),
        )
        return

    if action == "d" and parts:
        ymd = parts[0]
        await callback.message.answer(
            f"Откройте дату {_fmt_ymd(ymd)} через раздел «Записи».",
            reply_markup=_month_keyboard(0).as_markup(),
        )
        return

    if action == "rec":
        await _safe_edit_or_answer(
            callback,
            "Календарь записей:",
            reply_markup=_month_keyboard(0).as_markup(),
        )
        return

    if action == "cn":
        items = appointment_uc.admin_list_cancelled()
        await _send_appointment_list(
            callback.message,
            items,
            "Отменённые записи",
            include_cancelled_actions=True,
            page=0,
        )
        return

    if action == "ops_sc":
        sc = admin_ops_uc.get_schedule()
        b = InlineKeyboardBuilder()
        b.button(text="Изменить начало", callback_data=build_admin_ops_schedule_edit("open"))
        b.button(text="Изменить конец", callback_data=build_admin_ops_schedule_edit("close"))
        b.button(text="Календарь по дням", callback_data=build_admin_records())
        b.button(text="« Меню", callback_data=build_admin_home())
        b.adjust(1)
        await callback.message.answer(
            _schedule_text(sc) + "\n\nПо умолчанию суббота/воскресенье закрыты.",
            reply_markup=b.as_markup(),
        )
        return

    if action == "ops_si":
        info = admin_ops_uc.get_salon_info(settings.salon_address, settings.salon_contacts)
        b = InlineKeyboardBuilder()
        b.button(text="Изменить адрес", callback_data=build_admin_ops_salon_edit("address"))
        b.button(text="Изменить контакты", callback_data=build_admin_ops_salon_edit("contacts"))
        b.button(
            text=("Скрыть адрес" if info.show_address else "Показать адрес"),
            callback_data=build_admin_ops_salon_toggle("address"),
        )
        b.button(
            text=("Скрыть контакты" if info.show_contacts else "Показать контакты"),
            callback_data=build_admin_ops_salon_toggle("contacts"),
        )
        b.button(text="« Меню", callback_data=build_admin_home())
        b.adjust(1)
        await callback.message.answer(
            "<b>Настройки салона</b>\n\n"
            f"Адрес: {'вкл' if info.show_address else 'выкл'}\n"
            f"Контакты: {'вкл' if info.show_contacts else 'выкл'}\n\n"
            f"📍 {info.address_text or settings.salon_address}\n"
            f"📞 {info.contacts_text or settings.salon_contacts}",
            reply_markup=b.as_markup(),
        )
        return

    if action == "ops_si_ed" and parts:
        field = parts[0]
        if field == "address":
            await state.set_state(AdminStates.salon_address)
            await callback.message.answer("Введите новый адрес:")
            return
        if field == "contacts":
            await state.set_state(AdminStates.salon_contacts)
            await callback.message.answer("Введите новые контакты:")
            return

    if action == "ops_si_tg" and parts:
        field = parts[0]
        try:
            info = admin_ops_uc.get_salon_info(settings.salon_address, settings.salon_contacts)
            if field == "address":
                admin_ops_uc.update_salon_info(
                    default_address=settings.salon_address,
                    default_contacts=settings.salon_contacts,
                    show_address=not info.show_address,
                )
            elif field == "contacts":
                admin_ops_uc.update_salon_info(
                    default_address=settings.salon_address,
                    default_contacts=settings.salon_contacts,
                    show_contacts=not info.show_contacts,
                )
        except AppError as e:
            await callback.message.answer(error_to_user_message(e))
            return
        await callback.message.answer("Настройки обновлены.")
        return

    if action == "ops_sc_ed" and parts:
        field = parts[0]
        if field == "open":
            await state.set_state(AdminStates.schedule_open)
            await callback.message.answer("Введите новое начало дня (HH:MM), например 08:00")
            return
        if field == "close":
            await state.set_state(AdminStates.schedule_close)
            await callback.message.answer("Введите новый конец дня (HH:MM), например 20:00")
            return
        if field == "step":
            await callback.message.answer(
                "Шаг слотов определяется длительностью услуги в каталоге."
            )
            return

    if action == "ops_sv":
        items = admin_ops_uc.list_services()
        b = InlineKeyboardBuilder()
        if items:
            for item in items[:30]:
                b.button(
                    text=_service_line(item)[:60],
                    callback_data=build_admin_ops_service_open(item.service_id),
                )
        b.button(text="➕ Добавить услугу", callback_data=build_admin_ops_service_add())
        b.button(text="« Меню", callback_data=build_admin_home())
        b.adjust(1)
        if items:
            preview_lines = ["\n<b>Как увидит клиент:</b>\n"]
            for item in admin_ops_uc.list_active_services():
                preview_lines.append(
                    f"• {item.name}\n  Цена: {item.price_text}\n  Длительность: {_fmt_duration_minutes(item.duration_minutes)}\n"
                )
            preview = "\n".join(preview_lines).strip()
            text = "<b>Каталог услуг</b>\n\n" + preview
        else:
            text = "<b>Каталог услуг</b>\n\nПока пусто."
        await callback.message.answer(text, reply_markup=b.as_markup())
        return

    if action == "ops_sv_add":
        await state.set_state(AdminStates.service_add)
        b = InlineKeyboardBuilder()
        b.button(text="« Меню", callback_data=build_admin_home())
        b.adjust(1)
        await callback.message.answer(
            "Введите услугу (свободный формат).\n"
            "Пример: Название | Длительность_мин | Цена\n"
            "Пример: Стрижка, 60, 1500 ₽",
            reply_markup=b.as_markup(),
        )
        return

    if action == "ops_sv_o" and parts:
        service_id = parts[0]
        try:
            item = admin_ops_uc.get_service_or_raise(service_id)
        except AppError as e:
            await callback.message.answer(error_to_user_message(e))
            return
        b = InlineKeyboardBuilder()
        b.button(text="Изменить название", callback_data=build_admin_ops_service_edit(service_id, "name"))
        b.button(text="Изменить длительность", callback_data=build_admin_ops_service_edit(service_id, "duration"))
        b.button(text="Изменить цену", callback_data=build_admin_ops_service_edit(service_id, "price"))
        b.button(text="Деактивировать", callback_data=build_admin_ops_service_deactivate(service_id))
        if not item.is_active:
            b.button(text="Активировать", callback_data=build_admin_ops_service_toggle(service_id))
        b.button(text="Удалить услугу", callback_data=build_admin_ops_service_delete(service_id))
        b.button(text="« К услугам", callback_data=build_admin_ops_services())
        b.adjust(1)
        await callback.message.answer(_service_line(item), reply_markup=b.as_markup())
        return

    if action == "ops_sv_off" and parts:
        service_id = parts[0]
        try:
            admin_ops_uc.update_service(service_id, is_active=False)
        except AppError as e:
            await callback.message.answer(error_to_user_message(e))
            return
        await callback.message.answer("Услуга деактивирована.")
        return

    if action == "ops_sv_tg" and parts:
        service_id = parts[0]
        try:
            item = admin_ops_uc.get_service_or_raise(service_id)
            updated = admin_ops_uc.update_service(service_id, is_active=not item.is_active)
        except AppError as e:
            await callback.message.answer(error_to_user_message(e))
            return
        await callback.message.answer(
            f"Статус обновлён: {'активна' if updated.is_active else 'деактивирована'}."
        )
        return

    if action == "ops_sv_del" and parts:
        service_id = parts[0]
        try:
            admin_ops_uc.delete_service(service_id)
        except AppError as e:
            await callback.message.answer(error_to_user_message(e))
            return
        await callback.message.answer("Услуга удалена.")
        return

    if action == "ops_sv_ed" and len(parts) >= 2:
        service_id = parts[0]
        field = parts[1]
        await state.update_data(ops_service_id=service_id)
        if field == "name":
            await state.set_state(AdminStates.service_edit_name)
            await callback.message.answer("Введите новое название услуги:")
            return
        if field == "duration":
            await state.set_state(AdminStates.service_edit_duration)
            await callback.message.answer("Введите новую длительность в минутах:")
            return
        if field == "price":
            await state.set_state(AdminStates.service_edit_price)
            await callback.message.answer("Введите новую цену (текстом):")
            return

    if action == "ops_bl":
        items = admin_ops_uc.list_blacklist()
        b = InlineKeyboardBuilder()
        active = [x for x in items if x.is_active]
        if active:
            for entry in active[:30]:
                b.button(
                    text=f"❌ {_blacklist_line(entry)}"[:60],
                    callback_data=build_admin_ops_blacklist_deactivate(entry.entry_id),
                )
        b.button(text="➕ Добавить", callback_data=build_admin_ops_blacklist_add())
        b.button(text="« Меню", callback_data=build_admin_home())
        b.adjust(1)
        text = "<b>Blacklist</b>\n\n" if active else "<b>Blacklist</b>\n\nАктивных записей нет."
        await callback.message.answer(text, reply_markup=b.as_markup())
        return

    if action == "ops_bl_add":
        await state.set_state(AdminStates.blacklist_add)
        b = InlineKeyboardBuilder()
        b.button(text="« Меню", callback_data=build_admin_home())
        b.adjust(1)
        await callback.message.answer(
            "Введите user_id или телефон.\n"
            "Причина опциональна.\n\n"
            "Примеры:\n"
            "123456789\n"
            "+79990001122 | частые отмены\n"
            "123456789 | +79990001122 | спам",
            reply_markup=b.as_markup(),
        )
        return

    if action == "ops_bl_off" and parts:
        entry_id = parts[0]
        try:
            admin_ops_uc.deactivate_blacklist_entry(entry_id)
        except AppError as e:
            await callback.message.answer(error_to_user_message(e))
            return
        await callback.message.answer("Запись в blacklist деактивирована.")
        return

    if action == "sr":
        await state.set_state(AdminStates.search_query)
        await callback.message.answer(
            "Введите запрос: фрагмент имени, телефона или дату YYYYMMDD.",
            reply_markup=InlineKeyboardBuilder()
            .button(text="« Отмена", callback_data=build_admin_home())
            .as_markup(),
        )
        return

    if action == "mon":
        shift = 0
        if parts:
            try:
                shift = int(parts[0])
            except ValueError:
                shift = 0
        await _safe_edit_or_answer(
            callback,
            "Календарь записей:",
            reply_markup=_month_keyboard(shift).as_markup(),
        )
        return

    if action == "md" and parts:
        ymd = parts[0]
        try:
            items = appointment_uc.admin_list_for_date(ymd)
        except AppError as e:
            await callback.message.answer(error_to_user_message(e))
            return
        is_closed = admin_ops_uc.is_day_closed(ymd)
        day_sched = admin_ops_uc.get_effective_schedule_for_date(ymd)
        b = InlineKeyboardBuilder()
        for hhmm in _slots_for_schedule(
            day_sched.open_time_hhmm, day_sched.close_time_hhmm, day_sched.slot_minutes
        ):
            ap = _slot_is_occupied_for_items(items, admin_ops_uc, hhmm)
            label = f"{hhmm[:2]}:{hhmm[2:]}"
            if is_closed:
                b.button(text=f"{label} · выходной", callback_data="a1|noop")
                continue
            if ap is None:
                b.button(
                    text=f"{label} · свободно",
                    callback_data=build_admin_time_slot(ymd, hhmm),
                )
            else:
                b.button(
                    text=f"{label} · занято",
                    callback_data=build_admin_open(ap.appointment_id),
                )
        if is_closed:
            b.button(text="✅ Открыть день", callback_data=build_admin_day_toggle(ymd, True))
        else:
            b.button(text="⛔ Закрыть день", callback_data=build_admin_day_toggle(ymd, False))
            b.button(text="Изм. начало дня", callback_data=build_admin_day_edit(ymd, "open"))
            b.button(text="Изм. конец дня", callback_data=build_admin_day_edit(ymd, "close"))
            b.button(text="Изм. шаг", callback_data=build_admin_day_edit(ymd, "step"))
        b.button(text="« К календарю", callback_data=build_admin_month(0))
        b.button(text="« Меню", callback_data=build_admin_home())
        b.adjust(1)
        await _safe_edit_or_answer(
            callback,
            (
                f"Записи на {_fmt_ymd(ymd)}: {'день закрыт' if is_closed else 'выберите время'}\n"
                f"Диапазон: {_fmt_hhmm(day_sched.open_time_hhmm)}-{_fmt_hhmm(day_sched.close_time_hhmm)}, "
                f"шаг {_fmt_duration_minutes(day_sched.slot_minutes)}"
            ),
            reply_markup=b.as_markup(),
        )
        return

    if action == "ops_day_tg" and len(parts) >= 2:
        ymd = parts[0]
        open_day = parts[1] == "1"
        try:
            admin_ops_uc.set_day_closed(ymd, not open_day)
        except AppError as e:
            await callback.message.answer(error_to_user_message(e))
            return
        await callback.message.answer("День обновлён.")
        return

    if action == "ops_day_ed" and len(parts) >= 2:
        ymd = parts[0]
        field = parts[1]
        await state.update_data(day_ymd=ymd)
        if field == "open":
            await state.set_state(AdminStates.day_schedule_open)
            await callback.message.answer("Введите начало дня для этой даты (HH:MM):")
            return
        if field == "close":
            await state.set_state(AdminStates.day_schedule_close)
            await callback.message.answer("Введите конец дня для этой даты (HH:MM):")
            return
        if field == "step":
            await state.set_state(AdminStates.day_schedule_step)
            await callback.message.answer("Введите шаг слотов для этой даты (мин):")
            return

    if action == "ts" and len(parts) >= 2:
        await callback.answer("Записи на это время нет", show_alert=False)
        return

    if action == "bk":
        if parts == ["hm"]:
            await callback.message.answer(
                "Админ: записи",
                reply_markup=_home_keyboard().as_markup(),
            )
        elif parts == ["td"]:
            await callback.message.answer(
                "Календарь записей:",
                reply_markup=_month_keyboard(0).as_markup(),
            )
        elif parts == ["cn"]:
            items = appointment_uc.admin_list_cancelled()
            await _send_appointment_list(
                callback.message,
                items,
                "Отменённые записи",
                include_cancelled_actions=True,
                page=0,
            )
        elif parts[0] == "d" and len(parts) >= 2:
            ymd = parts[1]
            await callback.message.answer(
                f"Откройте дату {_fmt_ymd(ymd)} через раздел «Записи».",
                reply_markup=_month_keyboard(0).as_markup(),
            )
        return

    if action == "p" and parts:
        ap_id = parts[0]
        try:
            ap = appointment_uc.get_appointment_or_raise(ap_id)
        except AppError as e:
            await callback.message.answer(error_to_user_message(e))
            return
        await _send_appointment_card(callback.message, ap, settings)
        return

    if action == "x" and parts:
        ap_id = parts[0]
        try:
            _, already = appointment_uc.admin_cancel(ap_id)
        except AppError as e:
            await callback.message.answer(error_to_user_message(e))
            return
        msg = "Запись уже была отменена." if already else "Запись отменена."
        await callback.message.answer(msg)
        try:
            ap = appointment_uc.get_appointment_or_raise(ap_id)
            await _send_appointment_card(callback.message, ap, settings)
        except AppError:
            pass
        return

    if action == "bl_ap" and parts:
        ap_id = parts[0]
        try:
            ap = appointment_uc.get_appointment_or_raise(ap_id)
            exists_user = (
                ap.user_id is not None
                and admin_ops_uc.blacklist_repo.get_active_by_user_id(int(ap.user_id)) is not None
            )
            exists_phone = (
                bool(ap.phone_e164)
                and admin_ops_uc.blacklist_repo.get_active_by_phone(ap.phone_e164) is not None
            )
            if exists_user or exists_phone:
                await callback.message.answer("Клиент уже в активном blacklist.")
                return
            admin_ops_uc.add_blacklist_entry(
                user_id=int(ap.user_id) if ap.user_id is not None else None,
                phone_e164=ap.phone_e164 or None,
                reason=None,
            )
        except AppError as e:
            await callback.message.answer(error_to_user_message(e))
            return
        await callback.message.answer("Клиент добавлен в blacklist.")
        return

    if action == "cnp" and parts:
        try:
            page = max(int(parts[0]), 0)
        except Exception:
            page = 0
        items = appointment_uc.admin_list_cancelled()
        await _send_appointment_list(
            callback.message,
            items,
            "Отменённые записи",
            include_cancelled_actions=True,
            page=page,
        )
        return

    if action == "e" and parts:
        ap_id = parts[0]
        try:
            appointment_uc.get_appointment_or_raise(ap_id)
        except AppError as e:
            await callback.message.answer(error_to_user_message(e))
            return
        b = InlineKeyboardBuilder()
        for d in _admin_dates(settings, count=14):
            if admin_ops_uc.is_day_closed(d):
                continue
            b.button(text=f"📅 {_fmt_ymd(d)}", callback_data=build_admin_move_date(ap_id, d))
        b.button(text="« К записи", callback_data=build_admin_open(ap_id))
        b.adjust(1)
        await callback.message.answer("Перенос: выберите дату", reply_markup=b.as_markup())
        return

    if action == "mv" and parts:
        ap_id = parts[0]
        b = InlineKeyboardBuilder()
        for d in _admin_dates(settings, count=14):
            if admin_ops_uc.is_day_closed(d):
                continue
            b.button(text=f"📅 {_fmt_ymd(d)}", callback_data=build_admin_move_date(ap_id, d))
        b.button(text="« К записи", callback_data=build_admin_open(ap_id))
        b.adjust(1)
        await callback.message.answer("Перенос: выберите дату", reply_markup=b.as_markup())
        return

    if action == "mvd" and len(parts) >= 2:
        ap_id = parts[0]
        ymd = parts[1]
        try:
            ap = appointment_uc.get_appointment_or_raise(ap_id)
            items = appointment_uc.admin_list_for_date(ymd)
        except AppError as e:
            await callback.message.answer(error_to_user_message(e))
            return
        sched = admin_ops_uc.get_effective_schedule_for_date(ymd)
        step_minutes = admin_ops_uc.get_service_slot_step_minutes(ap.service_id)
        slots = _slots_for_schedule(sched.open_time_hhmm, sched.close_time_hhmm, step_minutes)
        b = InlineKeyboardBuilder()
        free_slots: list[str] = []
        for hhmm in slots:
            if _slot_is_occupied_for_items(items, admin_ops_uc, hhmm, exclude_appointment_id=ap.appointment_id):
                continue
            free_slots.append(hhmm)
            b.button(
                text=f"{hhmm[:2]}:{hhmm[2:]}",
                callback_data=build_admin_move_time(ap_id, ymd, hhmm),
            )
        b.button(text="« К датам", callback_data=build_admin_move_start(ap_id))
        b.button(text="« К записи", callback_data=build_admin_open(ap_id))
        b.adjust(1)
        if not free_slots:
            await callback.message.answer("Свободных слотов на эту дату нет.", reply_markup=b.as_markup())
            return
        await callback.message.answer(
            f"Перенос на {_fmt_ymd(ymd)}: выберите свободное время",
            reply_markup=b.as_markup(),
        )
        return

    if action == "mvt" and len(parts) >= 3:
        ap_id = parts[0]
        ymd = parts[1]
        hhmm = parts[2]
        new_start = f"{ymd}T{hhmm}"
        try:
            ap = appointment_uc.admin_update(ap_id, start_datetime_utc=new_start)
        except AppError as e:
            await callback.message.answer(error_to_user_message(e))
            return
        await callback.message.answer("Запись перенесена.")
        await _send_appointment_card(callback.message, ap, settings)
        return

    if action == "s" and len(parts) >= 2:
        ap_id = parts[0]
        try:
            idx = int(parts[1])
        except ValueError:
            await callback.message.answer("Некорректные данные.")
            return
        if idx < 0 or idx >= len(settings.services):
            await callback.message.answer("Некорректная услуга.")
            return
        svc = settings.services[idx]
        try:
            ap = appointment_uc.admin_update(ap_id, service_id=svc)
        except AppError as e:
            await callback.message.answer(error_to_user_message(e))
            return
        await callback.message.answer("Услуга обновлена.")
        await _send_appointment_card(callback.message, ap, settings)
        return

    if action == "dt" and len(parts) >= 2:
        ap_id = parts[0]
        ymd = parts[1]
        try:
            ap0 = appointment_uc.get_appointment_or_raise(ap_id)
        except AppError as e:
            await callback.message.answer(error_to_user_message(e))
            return
        old = ap0.start_datetime_utc or ""
        time_part = "1000"
        if "T" in old and len(old.split("T", 1)[1]) >= 4:
            time_part = old.split("T", 1)[1][:4]
        new_start = f"{ymd}T{time_part}"
        try:
            ap = appointment_uc.admin_update(ap_id, start_datetime_utc=new_start)
        except AppError as e:
            await callback.message.answer(error_to_user_message(e))
            return
        await callback.message.answer("Дата обновлена (время сохранено).")
        await _send_appointment_card(callback.message, ap, settings)
        return

    if action == "tm" and len(parts) >= 2:
        ap_id = parts[0]
        hhmm = parts[1]
        try:
            ap0 = appointment_uc.get_appointment_or_raise(ap_id)
        except AppError as e:
            await callback.message.answer(error_to_user_message(e))
            return
        old = ap0.start_datetime_utc or ""
        if "T" not in old or len(old) < 9:
            await callback.message.answer("Некорректная исходная дата записи.")
            return
        d_part = old.split("T", 1)[0]
        new_start = f"{d_part}T{hhmm}"
        try:
            ap = appointment_uc.admin_update(ap_id, start_datetime_utc=new_start)
        except AppError as e:
            await callback.message.answer(error_to_user_message(e))
            return
        await callback.message.answer("Время обновлено.")
        await _send_appointment_card(callback.message, ap, settings)
        return

    if action == "nm" and parts:
        await state.set_state(AdminStates.edit_name)
        await state.update_data(admin_aid=parts[0])
        await callback.message.answer("Введите новое имя (одним сообщением):")
        return

    if action == "ph" and parts:
        await state.set_state(AdminStates.edit_phone)
        await state.update_data(admin_aid=parts[0])
        await callback.message.answer("Введите новый телефон:")
        return

    await callback.message.answer("Команда устарела. Откройте /admin.")


async def _send_appointment_list(
    message: Message,
    items: list[Appointment],
    title: str,
    include_cancelled_actions: bool = False,
    page: int = 0,
) -> None:
    if not items:
        b = InlineKeyboardBuilder()
        b.button(text="« Меню", callback_data=build_admin_home())
        await message.answer(f"{title}: записей нет.", reply_markup=b.as_markup())
        return
    page_size = 8
    total_pages = max((len(items) - 1) // page_size + 1, 1)
    page = min(max(page, 0), total_pages - 1)
    start = page * page_size
    stop = start + page_size
    page_items = items[start:stop]

    header = f"<b>{title}</b> ({len(items)})\nСтраница {page + 1}/{total_pages}\n"
    if include_cancelled_actions:
        await message.answer(header.strip())
    else:
        def _row(a: Appointment) -> str:
            return f"• {format_slot_utc_for_user(a.start_datetime_utc)} — {a.service_id} — {a.customer_name} — {a.phone_e164}"
        text = header + "\n".join(_row(a) for a in page_items)
        await message.answer(text)

    b = InlineKeyboardBuilder()
    for ap in page_items:
        if include_cancelled_actions:
            line = (
                f"{format_slot_utc_for_user(ap.start_datetime_utc)} | "
                f"{ap.service_id} | {ap.customer_name} | {ap.phone_e164} | blacklist"
            )
            b.button(
                text=line[:64],
                callback_data=build_admin_blacklist_from_appointment(ap.appointment_id),
            )
        else:
            b.button(text=f"Открыть · {_btn_line(ap)}", callback_data=build_admin_open(ap.appointment_id))
    if total_pages > 1:
        if page > 0:
            b.button(text="« Пред.", callback_data=build_admin_cancelled_page(page - 1))
        if page < total_pages - 1:
            b.button(text="След. »", callback_data=build_admin_cancelled_page(page + 1))
    b.button(text="« Меню", callback_data=build_admin_home())
    b.adjust(1)
    await message.answer(("Записи:" if include_cancelled_actions else "Открыть запись:"), reply_markup=b.as_markup())


async def _send_appointment_card(
    message: Message,
    ap: Appointment,
    settings: Settings,
) -> None:
    b = InlineKeyboardBuilder()
    if ap.status != AppointmentStatus.CANCELLED:
        b.button(text="❌ Отменить", callback_data=build_admin_cancel_appt(ap.appointment_id))
        b.button(text="🔁 Перенести", callback_data=build_admin_move_start(ap.appointment_id))
    b.button(text="« Меню", callback_data=build_admin_home())
    b.adjust(1)
    await message.answer(_card_text(ap, settings), reply_markup=b.as_markup())


@router.message(AdminStates.search_query, F.text)
async def admin_search_run(
    message: Message,
    state: FSMContext,
    settings: Settings,
    appointment_uc: AppointmentUseCases,
) -> None:
    uid = message.from_user.id if message.from_user else None
    if not _is_admin(uid, settings):
        await state.clear()
        return
    q = (message.text or "").strip()
    await state.clear()
    items = appointment_uc.admin_search(q)
    await _send_appointment_list(
        message,
        items,
        f"Поиск: {q}",
    )


@router.message(AdminStates.schedule_open, F.text)
async def admin_schedule_set_open(
    message: Message,
    state: FSMContext,
    settings: Settings,
    admin_ops_uc: AdminOpsUseCases,
) -> None:
    uid = message.from_user.id if message.from_user else None
    if not _is_admin(uid, settings):
        await state.clear()
        return
    value = (message.text or "").strip()
    try:
        admin_ops_uc.update_schedule(open_time_hhmm=value)
    except AppError as e:
        await message.answer(error_to_user_message(e))
        return
    await state.clear()
    await message.answer("Начало дня обновлено.")


@router.message(AdminStates.schedule_close, F.text)
async def admin_schedule_set_close(
    message: Message,
    state: FSMContext,
    settings: Settings,
    admin_ops_uc: AdminOpsUseCases,
) -> None:
    uid = message.from_user.id if message.from_user else None
    if not _is_admin(uid, settings):
        await state.clear()
        return
    value = (message.text or "").strip()
    try:
        admin_ops_uc.update_schedule(close_time_hhmm=value)
    except AppError as e:
        await message.answer(error_to_user_message(e))
        return
    await state.clear()
    await message.answer("Конец дня обновлён.")


@router.message(AdminStates.schedule_step, F.text)
async def admin_schedule_set_step(
    message: Message,
    state: FSMContext,
    settings: Settings,
    admin_ops_uc: AdminOpsUseCases,
) -> None:
    uid = message.from_user.id if message.from_user else None
    if not _is_admin(uid, settings):
        await state.clear()
        return
    value = (message.text or "").strip()
    try:
        step = int(value)
        admin_ops_uc.update_schedule(slot_minutes=step)
    except ValueError:
        await message.answer("Введите целое число минут.")
        return
    except AppError as e:
        await message.answer(error_to_user_message(e))
        return
    await state.clear()
    await message.answer("Шаг слотов обновлён.")


@router.message(AdminStates.day_schedule_open, F.text)
async def admin_day_schedule_set_open(
    message: Message,
    state: FSMContext,
    settings: Settings,
    admin_ops_uc: AdminOpsUseCases,
) -> None:
    uid = message.from_user.id if message.from_user else None
    if not _is_admin(uid, settings):
        await state.clear()
        return
    data = await state.get_data()
    ymd = str(data.get("day_ymd") or "")
    if not ymd:
        await state.clear()
        await message.answer("Сессия устарела.")
        return
    try:
        admin_ops_uc.update_day_schedule(ymd, open_time_hhmm=(message.text or "").strip())
    except AppError as e:
        await message.answer(error_to_user_message(e))
        return
    await state.clear()
    await message.answer("Начало дня обновлено.")


@router.message(AdminStates.day_schedule_close, F.text)
async def admin_day_schedule_set_close(
    message: Message,
    state: FSMContext,
    settings: Settings,
    admin_ops_uc: AdminOpsUseCases,
) -> None:
    uid = message.from_user.id if message.from_user else None
    if not _is_admin(uid, settings):
        await state.clear()
        return
    data = await state.get_data()
    ymd = str(data.get("day_ymd") or "")
    if not ymd:
        await state.clear()
        await message.answer("Сессия устарела.")
        return
    try:
        admin_ops_uc.update_day_schedule(ymd, close_time_hhmm=(message.text or "").strip())
    except AppError as e:
        await message.answer(error_to_user_message(e))
        return
    await state.clear()
    await message.answer("Конец дня обновлён.")


@router.message(AdminStates.day_schedule_step, F.text)
async def admin_day_schedule_set_step(
    message: Message,
    state: FSMContext,
    settings: Settings,
    admin_ops_uc: AdminOpsUseCases,
) -> None:
    uid = message.from_user.id if message.from_user else None
    if not _is_admin(uid, settings):
        await state.clear()
        return
    data = await state.get_data()
    ymd = str(data.get("day_ymd") or "")
    if not ymd:
        await state.clear()
        await message.answer("Сессия устарела.")
        return
    try:
        admin_ops_uc.update_day_schedule(ymd, slot_minutes=int((message.text or "").strip()))
    except ValueError:
        await message.answer("Введите целое число минут.")
        return
    except AppError as e:
        await message.answer(error_to_user_message(e))
        return
    await state.clear()
    await message.answer("Шаг слотов для дня обновлён.")


@router.message(AdminStates.service_add, F.text)
async def admin_service_add(
    message: Message,
    state: FSMContext,
    settings: Settings,
    admin_ops_uc: AdminOpsUseCases,
) -> None:
    uid = message.from_user.id if message.from_user else None
    if not _is_admin(uid, settings):
        await state.clear()
        return
    raw = (message.text or "").strip()
    if not raw:
        await message.answer(
            "Не удалось распознать услугу. Пример: Название | Длительность_мин | Цена",
            reply_markup=_menu_only_kb(),
        )
        return
    parts = [x.strip() for x in raw.split("|") if x.strip()]
    if len(parts) < 3:
        parts = [x.strip() for x in raw.split(",") if x.strip()]
    if len(parts) < 3:
        tokens = [x for x in raw.split() if x.strip()]
        if len(tokens) >= 3:
            dur_idx = next((i for i, tok in enumerate(tokens) if tok.isdigit()), -1)
            if 0 < dur_idx < len(tokens) - 1:
                parts = [
                    " ".join(tokens[:dur_idx]).strip(),
                    tokens[dur_idx],
                    " ".join(tokens[dur_idx + 1 :]).strip(),
                ]
    if len(parts) < 3:
        await message.answer(
            "Не удалось распознать формат. Пример: Название | Длительность_мин | Цена",
            reply_markup=_menu_only_kb(),
        )
        return
    name, dur_raw, price = parts[0], parts[1], " | ".join(parts[2:])
    try:
        duration = int(dur_raw)
        admin_ops_uc.add_service(name, duration, price)
    except ValueError:
        await message.answer("Длительность должна быть числом. Пример: Стрижка | 60 | 1500 ₽")
        return
    except AppError as e:
        await message.answer(error_to_user_message(e))
        return
    await state.clear()
    await message.answer("Услуга добавлена.")


@router.message(AdminStates.service_edit_name, F.text)
async def admin_service_edit_name(
    message: Message,
    state: FSMContext,
    settings: Settings,
    admin_ops_uc: AdminOpsUseCases,
) -> None:
    uid = message.from_user.id if message.from_user else None
    if not _is_admin(uid, settings):
        await state.clear()
        return
    data = await state.get_data()
    service_id = str(data.get("ops_service_id") or "")
    if not service_id:
        await state.clear()
        await message.answer("Сессия устарела.")
        return
    try:
        admin_ops_uc.update_service(service_id, name=(message.text or "").strip())
    except AppError as e:
        await message.answer(error_to_user_message(e))
        return
    await state.clear()
    await message.answer("Название услуги обновлено.")


@router.message(AdminStates.service_edit_duration, F.text)
async def admin_service_edit_duration(
    message: Message,
    state: FSMContext,
    settings: Settings,
    admin_ops_uc: AdminOpsUseCases,
) -> None:
    uid = message.from_user.id if message.from_user else None
    if not _is_admin(uid, settings):
        await state.clear()
        return
    data = await state.get_data()
    service_id = str(data.get("ops_service_id") or "")
    if not service_id:
        await state.clear()
        await message.answer("Сессия устарела.")
        return
    try:
        duration = int((message.text or "").strip())
        admin_ops_uc.update_service(service_id, duration_minutes=duration)
    except ValueError:
        await message.answer("Введите целое число минут.")
        return
    except AppError as e:
        await message.answer(error_to_user_message(e))
        return
    await state.clear()
    await message.answer("Длительность услуги обновлена.")


@router.message(AdminStates.service_edit_price, F.text)
async def admin_service_edit_price(
    message: Message,
    state: FSMContext,
    settings: Settings,
    admin_ops_uc: AdminOpsUseCases,
) -> None:
    uid = message.from_user.id if message.from_user else None
    if not _is_admin(uid, settings):
        await state.clear()
        return
    data = await state.get_data()
    service_id = str(data.get("ops_service_id") or "")
    if not service_id:
        await state.clear()
        await message.answer("Сессия устарела.")
        return
    try:
        admin_ops_uc.update_service(service_id, price_text=(message.text or "").strip())
    except AppError as e:
        await message.answer(error_to_user_message(e))
        return
    await state.clear()
    await message.answer("Цена услуги обновлена.")


@router.message(AdminStates.blacklist_add, F.text)
async def admin_blacklist_add(
    message: Message,
    state: FSMContext,
    settings: Settings,
    admin_ops_uc: AdminOpsUseCases,
) -> None:
    uid = message.from_user.id if message.from_user else None
    if not _is_admin(uid, settings):
        await state.clear()
        return
    raw = (message.text or "").strip()
    if not raw:
        await message.answer(
            "Введите user_id или телефон. Причина опциональна.",
            reply_markup=_menu_only_kb(),
        )
        return
    parts = [x.strip() for x in raw.split("|") if x.strip()]
    user_id: int | None = None
    phone: str | None = None
    reason: str | None = None

    if len(parts) == 1:
        token = parts[0]
        if token.isdigit():
            user_id = int(token)
        else:
            phone = token
    elif len(parts) == 2:
        first, second = parts
        if first.isdigit():
            user_id = int(first)
            reason = second or None
        else:
            phone = first
            reason = second or None
    elif len(parts) >= 3:
        if parts[0].isdigit():
            user_id = int(parts[0])
        phone = parts[1] or None
        reason = " | ".join(parts[2:]).strip() or None
    else:
        await message.answer(
            "Не удалось распознать ввод. Пример: 123456789 | причина",
            reply_markup=_menu_only_kb(),
        )
        return

    try:
        admin_ops_uc.add_blacklist_entry(user_id=user_id, phone_e164=phone, reason=reason)
    except AppError as e:
        await message.answer(error_to_user_message(e))
        return
    await state.clear()
    await message.answer("Запись в blacklist добавлена.")


@router.message(AdminStates.salon_address, F.text)
async def admin_salon_set_address(
    message: Message,
    state: FSMContext,
    settings: Settings,
    admin_ops_uc: AdminOpsUseCases,
) -> None:
    uid = message.from_user.id if message.from_user else None
    if not _is_admin(uid, settings):
        await state.clear()
        return
    try:
        admin_ops_uc.update_salon_info(
            default_address=settings.salon_address,
            default_contacts=settings.salon_contacts,
            address_text=(message.text or "").strip(),
        )
    except AppError as e:
        await message.answer(error_to_user_message(e))
        return
    await state.clear()
    await message.answer("Адрес обновлён.")


@router.message(AdminStates.salon_contacts, F.text)
async def admin_salon_set_contacts(
    message: Message,
    state: FSMContext,
    settings: Settings,
    admin_ops_uc: AdminOpsUseCases,
) -> None:
    uid = message.from_user.id if message.from_user else None
    if not _is_admin(uid, settings):
        await state.clear()
        return
    try:
        admin_ops_uc.update_salon_info(
            default_address=settings.salon_address,
            default_contacts=settings.salon_contacts,
            contacts_text=(message.text or "").strip(),
        )
    except AppError as e:
        await message.answer(error_to_user_message(e))
        return
    await state.clear()
    await message.answer("Контакты обновлены.")


@router.message(AdminStates.edit_name, F.text)
async def admin_save_name(
    message: Message,
    state: FSMContext,
    settings: Settings,
    appointment_uc: AppointmentUseCases,
) -> None:
    uid = message.from_user.id if message.from_user else None
    if not _is_admin(uid, settings):
        await state.clear()
        return
    data = await state.get_data()
    aid = str(data.get("admin_aid") or "")
    await state.clear()
    if not aid:
        await message.answer("Сессия устарела.")
        return
    name = (message.text or "").strip()
    try:
        ap = appointment_uc.admin_update(aid, customer_name=name)
    except AppError as e:
        await message.answer(error_to_user_message(e))
        return
    await message.answer("Имя сохранено.")
    await _send_appointment_card(message, ap, settings)


@router.message(AdminStates.edit_phone, F.text)
async def admin_save_phone(
    message: Message,
    state: FSMContext,
    settings: Settings,
    appointment_uc: AppointmentUseCases,
) -> None:
    uid = message.from_user.id if message.from_user else None
    if not _is_admin(uid, settings):
        await state.clear()
        return
    data = await state.get_data()
    aid = str(data.get("admin_aid") or "")
    await state.clear()
    if not aid:
        await message.answer("Сессия устарела.")
        return
    phone = (message.text or "").strip()
    try:
        ap = appointment_uc.admin_update(aid, phone_e164=phone)
    except AppError as e:
        await message.answer(error_to_user_message(e))
        return
    await message.answer("Телефон сохранён.")
    await _send_appointment_card(message, ap, settings)
