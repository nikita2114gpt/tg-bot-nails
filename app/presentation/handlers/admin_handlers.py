from __future__ import annotations

from datetime import date, timedelta

from aiogram import F, Router
from aiogram.filters import Command, StateFilter, or_f
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.application.admin_ops_uc import AdminOpsUseCases
from app.application.appointment_uc import AppointmentUseCases, format_slot_utc_for_user
from app.application.booking_uc import BookingUseCases
from app.application.service_catalog_view import resolve_service_price_and_duration
from app.application.service_catalog_view import format_service_block
from app.config import Settings
from app.core.errors import AppError, error_to_user_message
from app.domain.enums import AppointmentStatus
from app.domain.models import Appointment
from app.domain.ops_models import BlacklistEntry, PriceListItem, ScheduleSettings, ServiceCatalogItem
from app.presentation.callback.nav_callbacks import (
    build_admin_cancel_appt,
    build_admin_edit_menu,
    build_admin_edit_name,
    build_admin_edit_phone,
    build_admin_home,
    build_admin_month,
    build_admin_month_date,
    build_admin_open,
    build_admin_active,
    build_admin_active_page,
    build_admin_blacklist_from_appointment,
    build_admin_cancelled_page,
    build_admin_records,
    build_admin_search,
    build_admin_set_date,
    build_admin_set_service,
    build_admin_set_time,
    build_admin_slot_open,
    build_admin_slot_toggle,
    build_admin_cancelled,
    build_admin_ops_blacklist,
    build_admin_ops_blacklist_add,
    build_admin_ops_blacklist_deactivate,
    build_admin_ops_blacklist_open,
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
    build_admin_ops_schedule_slot_step,
    build_admin_ops_service_delete,
    build_admin_ops_service_add,
    build_admin_ops_service_deactivate,
    build_admin_ops_service_edit,
    build_admin_ops_service_open,
    build_admin_ops_service_toggle,
    build_admin_ops_services,
    build_admin_ops_price_activate,
    build_admin_ops_price_add,
    build_admin_ops_price_deactivate,
    build_admin_ops_price_delete,
    build_admin_ops_price_edit,
    build_admin_ops_price_list,
    build_admin_ops_price_open,
    parse_admin,
)
from app.presentation.fsm.booking_keys import ADMIN_ASSISTED_BOOKING_KEY
from app.presentation.fsm.states import AdminStates, BookingStates
from app.presentation.keyboards.admin_reply_kb import (
    BTN_ADMIN_HOME,
    BTN_ADMIN_TO_CLIENT,
    admin_reply_keyboard,
)
from app.presentation.handlers.booking_handlers import (
    _available_dates,
    _calendar_holiday_and_fully_busy_sets,
)
from app.presentation.keyboards.booking_kb import admin_move_date_keyboard, service_keyboard
from app.presentation.keyboards.main_menu_kb import main_menu_reply_keyboard

router = Router(name="admin")

# Подсказка при ошибке ввода в FSM админки (команды /cancel и /start обрабатываются отдельно, раньше F.text).
_ADMIN_FSM_TIME_HINT = (
    "\n\nПовторите ввод или отправьте /cancel для выхода из режима."
)
# Второе сообщение — только inline-клавиатура. Telegram отклоняет текст из одних невидимых символов.
_ADMIN_INLINE_SECTIONS_CAPTION = "Разделы:"

# Текст сообщения с сеткой месяца (легенда админ-календаря).
_ADMIN_CALENDAR_TITLE_AND_LEGEND = (
    "Календарь 📅\n\n"
    "Подсказка: 🚫 выходной • 🔒 занято • ◔ сокращённый день\n"
    "⏺ сокращённый день занят"
)

_ADMIN_MOVE_DATE_SCREEN = (
    "Перенос: выберите дату\n\n"
    "🚫 — выходной\n"
    "🔒 — занято\n\n"
    "Подсказка: 🚫 выходной • 🔒 занято"
)


def _is_admin(user_id: int | None, settings: Settings) -> bool:
    return user_id is not None and user_id in set(settings.admin_ids)


def _month_keyboard(
    month_shift: int = 0,
    is_day_closed_fn=None,
    is_short_day_fn=None,
    appointment_uc: AppointmentUseCases | None = None,
    admin_ops_uc: AdminOpsUseCases | None = None,
) -> InlineKeyboardBuilder:
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
    for wd in ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"):
        b.button(text=wd, callback_data="a1|noop")

    leading_pad = start.weekday()
    for _ in range(leading_pad):
        b.button(text="·", callback_data="a1|noop")

    items_by_ymd: dict[str, list[Appointment]] | None = None
    if appointment_uc is not None and admin_ops_uc is not None:
        yset = {(start + timedelta(days=i)).strftime("%Y%m%d") for i in range(30)}
        items_by_ymd = _admin_items_by_ymd_from_repo(appointment_uc, yset)

    for i in range(30):
        d = start + timedelta(days=i)
        ymd = d.strftime("%Y%m%d")
        label = d.strftime("%d")
        try:
            if callable(is_day_closed_fn) and is_day_closed_fn(ymd):
                label = f"🚫{label}"
            else:
                is_short = callable(is_short_day_fn) and is_short_day_fn(ymd)
                is_full = (
                    items_by_ymd is not None
                    and admin_ops_uc is not None
                    and _admin_day_open_and_all_slots_taken(ymd, items_by_ymd.get(ymd, []), admin_ops_uc)
                )
                if is_short and is_full:
                    label = f"⏺{label}"
                elif is_short:
                    label = f"◔{label}"
                elif is_full:
                    label = f"🔒{label}"
        except Exception:
            pass
        b.button(text=label, callback_data=build_admin_month_date(ymd))
    b.button(text="« Меню", callback_data=build_admin_home())
    grid_cells = leading_pad + 30
    rows = grid_cells // 7
    rem = grid_cells % 7
    adjust_pattern: list[int] = [3, 7]
    if rows > 0:
        adjust_pattern.extend([7] * rows)
    if rem:
        adjust_pattern.append(rem)
    adjust_pattern.append(1)
    b.adjust(*adjust_pattern)
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
    b.button(text="📋 Записи", callback_data=build_admin_records())
    b.button(text="📌 Активные записи", callback_data=build_admin_active())
    b.button(text="❌ Отменённые", callback_data=build_admin_cancelled())
    b.button(text="🔍 Поиск", callback_data=build_admin_search())
    b.button(text="🗓 Расписание", callback_data=build_admin_ops_schedule())
    b.button(text="📍 Адрес / Контакты", callback_data=build_admin_ops_salon())
    b.button(text="💎 Услуги", callback_data=build_admin_ops_services())
    b.button(text="💸 Прайс", callback_data=build_admin_ops_price_list())
    b.button(text="⛔ Blacklist", callback_data=build_admin_ops_blacklist())
    b.button(text="➕ Добавить клиента", callback_data="a1|adm_cli")
    b.adjust(1, 2, 2, 2, 2, 1)
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


def _admin_items_by_ymd_from_repo(
    appointment_uc: AppointmentUseCases,
    ymds: set[str],
) -> dict[str, list[Appointment]]:
    out: dict[str, list[Appointment]] = {d: [] for d in ymds}
    try:
        repo = appointment_uc.appointment_repo
        list_all = getattr(repo, "list_all", None)
        if not callable(list_all):
            return out
        for ap in list_all():
            if ap.status == AppointmentStatus.CANCELLED:
                continue
            s = ap.start_datetime_utc or ""
            if len(s) < 8:
                continue
            ymd = s[:8]
            if ymd in ymds:
                out[ymd].append(ap)
    except Exception:
        pass
    return out


def _admin_day_open_and_all_slots_taken(
    ymd: str,
    items: list[Appointment],
    admin_ops_uc: AdminOpsUseCases,
) -> bool:
    if admin_ops_uc.is_day_closed(ymd):
        return False
    ds = admin_ops_uc.get_effective_schedule_for_date(ymd)
    slots = _slots_for_schedule(ds.open_time_hhmm, ds.close_time_hhmm, ds.slot_minutes)
    if not slots:
        return True
    return all(_slot_is_occupied_for_items(items, admin_ops_uc, hhmm) is not None for hhmm in slots)


def _slot_is_occupied_for_items(
    items: list[Appointment],
    admin_ops_uc: AdminOpsUseCases,
    hhmm: str,
    date_yyyymmdd: str | None = None,
    exclude_appointment_id: str | None = None,
    requested_service_id: str | None = None,
    workday_end_hhmm: str | None = None,
) -> Appointment | None:
    slot_min = _hhmm_to_minutes(hhmm)
    if slot_min is None:
        return None
    workday_end_min = _hhmm_to_minutes(workday_end_hhmm) if workday_end_hhmm else None
    if requested_service_id:
        try:
            requested_duration_min = max(
                int(admin_ops_uc.get_service_slot_step_minutes(requested_service_id)),
                5,
            )
        except Exception:
            requested_duration_min = 60
        try:
            if date_yyyymmdd and admin_ops_uc.is_service_interval_blocked_by_disabled_slots(
                date_yyyymmdd,
                hhmm,
                requested_duration_min,
            ):
                return Appointment(
                    appointment_id="__disabled_slot_block__",
                    draft_id="",
                    user_id=0,
                    service_id=requested_service_id,
                    start_datetime_utc="",
                    customer_name="",
                    phone_e164="",
                )
        except Exception:
            pass
        if workday_end_min is not None and (slot_min + requested_duration_min) > workday_end_min:
            return Appointment(
                appointment_id="__workday_end_block__",
                draft_id="",
                user_id=0,
                service_id=requested_service_id,
                start_datetime_utc="",
                customer_name="",
                phone_e164="",
            )
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
        try:
            requested_duration_for_overlap = max(
                int(admin_ops_uc.get_service_slot_step_minutes(requested_service_id or ap.service_id)),
                5,
            )
        except Exception:
            requested_duration_for_overlap = 60
        new_start = slot_min
        new_end = slot_min + requested_duration_for_overlap
        if not (new_end <= start_min or new_start >= end_min):
            return ap
    return None


def _schedule_text(value: ScheduleSettings) -> str:
    return (
        "<b>Управление расписанием ✨</b>\n"
        "Настройки ниже.\n\n"
        f"Начало дня: {_fmt_hhmm(value.open_time_hhmm)}\n"
        f"Последний слот: {_fmt_hhmm(value.close_time_hhmm)}\n"
        f"Конец рабочего дня: {_fmt_hhmm(value.workday_end_time_hhmm)}\n"
        f"Шаг слотов: {_fmt_duration_minutes(value.slot_minutes)}"
    )


def _service_line(item: ServiceCatalogItem) -> str:
    mark = "🟢" if item.is_active else "⚪"
    dur = _fmt_duration_minutes(item.duration_minutes)
    return f"{mark} {item.name} · {dur} · {item.price_text}"


def _price_line(item: PriceListItem) -> str:
    mark = "🟢" if item.is_active else "⚪"
    short = (item.display_text or "").replace("\n", " ")
    if len(short) > 80:
        short = short[:77] + "..."
    return f"{mark} {short}"


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


def _active_list_button_text(ap: Appointment) -> str:
    admin_prefix = "🛠 " if int(ap.user_id or 0) == 0 else ""
    sdt = ap.start_datetime_utc or ""
    ddmm = "—"
    hm = "—"
    if len(sdt) >= 13 and "T" in sdt:
        d, t = sdt.split("T", 1)
        if len(d) == 8 and d.isdigit():
            ddmm = f"{d[6:8]}.{d[4:6]}"
        if len(t) >= 4:
            hm = f"{t[:2]}:{t[2:4]}"
    name = ((ap.customer_name or "").strip() or "—")[:20]
    phone = ((ap.phone_e164 or "").strip() or "—")[:22]
    line = f"{admin_prefix}{ddmm} {hm} — {name} — {phone}"
    return line[:64]


def _card_text(ap: Appointment, settings: Settings, admin_ops_uc: AdminOpsUseCases | None = None) -> str:
    when = format_slot_utc_for_user(ap.start_datetime_utc)
    phone = ap.phone_e164 or "—"
    st = ap.status.value
    price, duration = resolve_service_price_and_duration(
        getattr(admin_ops_uc, "service_repo", None),
        str(ap.service_id or ""),
    )
    admin_line = "\n\n🛠 Создано админом" if int(ap.user_id) == 0 else ""
    return (
        f"<b>Детали записи 📄</b> ({st})\n\n"
        f"📅 {when}\n"
        f"{format_service_block(ap.service_id or '—', price, duration)}\n"
        f"👤 {ap.customer_name}\n"
        f"📞 {phone}"
        f"{admin_line}"
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
async def admin_entry(message: Message, state: FSMContext, settings: Settings) -> None:
    uid = message.from_user.id if message.from_user else None
    if not _is_admin(uid, settings):
        await message.answer("Недостаточно прав.")
        return
    await state.clear()
    await message.answer(
        "Панель управления ✨\nВыберите нужный раздел.",
        reply_markup=admin_reply_keyboard(),
    )
    await message.answer(
        _ADMIN_INLINE_SECTIONS_CAPTION,
        reply_markup=_home_keyboard().as_markup(),
        parse_mode=None,
    )


@router.message(
    or_f(
        F.text == BTN_ADMIN_HOME,
        F.text == "📋 Админ: меню",
        F.text == "Админ: меню",
        F.text == "📁 Админ: меню",
    )
)
async def admin_reply_menu_button(
    message: Message,
    state: FSMContext,
    settings: Settings,
) -> None:
    uid = message.from_user.id if message.from_user else None
    if not _is_admin(uid, settings):
        return
    await state.clear()
    await message.answer(
        "Панель управления ✨\nВыберите нужный раздел.",
        reply_markup=admin_reply_keyboard(),
    )
    await message.answer(
        _ADMIN_INLINE_SECTIONS_CAPTION,
        reply_markup=_home_keyboard().as_markup(),
        parse_mode=None,
    )


@router.message(Command("cancel"), StateFilter(AdminStates))
async def admin_fsm_cancel(message: Message, state: FSMContext, settings: Settings) -> None:
    uid = message.from_user.id if message.from_user else None
    if not _is_admin(uid, settings):
        await state.clear()
        return
    await state.clear()
    await message.answer(
        "Ввод отменён. /admin — меню администратора.",
        reply_markup=admin_reply_keyboard(),
    )


@router.message(Command("start"), StateFilter(AdminStates))
async def admin_fsm_exit_to_client_menu(message: Message, state: FSMContext, settings: Settings) -> None:
    uid = message.from_user.id if message.from_user else None
    if not _is_admin(uid, settings):
        await state.clear()
        return
    await state.clear()
    await message.answer(
        "Клиентское меню открыто ✨",
        reply_markup=main_menu_reply_keyboard(),
    )


@router.message(F.text == BTN_ADMIN_TO_CLIENT)
async def admin_goto_client_menu(message: Message, state: FSMContext, settings: Settings) -> None:
    uid = message.from_user.id if message.from_user else None
    if not _is_admin(uid, settings):
        return
    # Тот же путь, что и Command("start") в AdminStates — без дублирования логики.
    await admin_fsm_exit_to_client_menu(message, state, settings)


@router.callback_query(F.data.startswith("a1"))
async def admin_callback(
    callback: CallbackQuery,
    state: FSMContext,
    settings: Settings,
    appointment_uc: AppointmentUseCases,
    admin_ops_uc: AdminOpsUseCases,
    booking_uc: BookingUseCases,
) -> None:
    await _safe_cq_answer(callback)
    if callback.message is None:
        return
    uid = callback.from_user.id if callback.from_user else None
    if not _is_admin(uid, settings):
        await callback.message.answer(
            "Недостаточно прав.",
            reply_markup=admin_reply_keyboard(),
        )
        return

    try:
        action, parts = parse_admin(callback.data or "")
    except ValueError:
        await callback.message.answer(
            "Команда устарела. Откройте /admin заново.",
            reply_markup=admin_reply_keyboard(),
        )
        return

    if action == "adm_cli":
        await state.clear()
        draft = booking_uc.start_booking_for_operator(uid)
        await state.set_state(BookingStates.choose_service)
        await state.update_data({ADMIN_ASSISTED_BOOKING_KEY: True})
        await callback.message.answer(
            "Запись клиента — шаг 1/5: выберите услугу.",
            reply_markup=service_keyboard(
                draft.draft_id,
                booking_uc.list_available_services(),
                getattr(booking_uc, "service_catalog_repo", None),
            ),
        )
        return

    await state.clear()

    def _is_short_day(ymd: str) -> bool:
        try:
            if admin_ops_uc.is_day_closed(ymd):
                return False
        except Exception:
            return False
        try:
            ov = admin_ops_uc.get_day_override(ymd)
        except Exception:
            ov = None
        if ov is not None and not bool(getattr(ov, "is_closed", False)):
            if (
                getattr(ov, "open_time_hhmm", None) is not None
                or getattr(ov, "close_time_hhmm", None) is not None
                or getattr(ov, "slot_minutes", None) is not None
            ):
                return True
        try:
            if admin_ops_uc.day_has_disabled_slot(ymd):
                return True
        except Exception:
            pass
        return False

    if action == "home":
        await callback.message.answer(
            "Панель управления ✨\nВыберите нужный раздел.",
            reply_markup=_home_keyboard().as_markup(),
        )
        return

    if action == "noop":
        # callback уже подтверждён в _safe_cq_answer — повторный answer даёт ошибку API
        return

    if action == "td":
        await callback.message.answer(
            _ADMIN_CALENDAR_TITLE_AND_LEGEND,
            reply_markup=_month_keyboard(0, admin_ops_uc.is_day_closed, _is_short_day, appointment_uc, admin_ops_uc).as_markup(),
        )
        return

    if action == "cal":
        await callback.message.answer(
            _ADMIN_CALENDAR_TITLE_AND_LEGEND,
            reply_markup=_month_keyboard(0, admin_ops_uc.is_day_closed, _is_short_day, appointment_uc, admin_ops_uc).as_markup(),
        )
        return

    if action == "d" and parts:
        ymd = parts[0]
        await callback.message.answer(
            f"Откройте дату {_fmt_ymd(ymd)} через раздел «Записи».",
            reply_markup=_month_keyboard(0, admin_ops_uc.is_day_closed, _is_short_day, appointment_uc, admin_ops_uc).as_markup(),
        )
        return

    if action == "rec":
        await _safe_edit_or_answer(
            callback,
            _ADMIN_CALENDAR_TITLE_AND_LEGEND,
            reply_markup=_month_keyboard(0, admin_ops_uc.is_day_closed, _is_short_day, appointment_uc, admin_ops_uc).as_markup(),
        )
        return

    if action == "cn":
        items = appointment_uc.admin_list_cancelled()
        await _send_appointment_list(
            callback.message,
            items,
            "Отменённые записи 🚫\nНиже отображается история отмен.",
            include_cancelled_actions=True,
            page=0,
        )
        return

    if action == "ac":
        items = appointment_uc.admin_list_active()
        await _send_appointment_list(
            callback.message,
            items,
            "Текущие записи ✨\nВот все подтверждённые записи.",
            active_list=True,
            page=0,
        )
        return

    if action == "ops_sc":
        sc = admin_ops_uc.get_schedule()
        b = InlineKeyboardBuilder()
        b.button(text="Изменить начало", callback_data=build_admin_ops_schedule_edit("open"))
        b.button(text="Изменить последний слот", callback_data=build_admin_ops_schedule_edit("close"))
        b.button(
            text="Изменить конец рабочего дня",
            callback_data=build_admin_ops_schedule_edit("workday_end"),
        )
        b.button(text="Шаг слотов", callback_data=build_admin_ops_schedule_slot_step())
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
            await callback.message.answer(
                "Введите новый адрес:",
                reply_markup=admin_reply_keyboard(),
            )
            return
        if field == "contacts":
            await state.set_state(AdminStates.salon_contacts)
            await callback.message.answer(
                "Введите новые контакты:",
                reply_markup=admin_reply_keyboard(),
            )
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
            await callback.message.answer(
                error_to_user_message(e),
                reply_markup=admin_reply_keyboard(),
            )
            return
        await callback.message.answer(
            "Настройки обновлены.",
            reply_markup=admin_reply_keyboard(),
        )
        return

    if action == "ops_sc_ed" and parts:
        field = parts[0]
        if field == "open":
            await state.set_state(AdminStates.schedule_open)
            await callback.message.answer(
                "⚠️ <b>Внимание</b>\n\n"
                "Изменение общего начала дня приведёт все индивидуальные настройки "
                "времени начала по конкретным дням к одному формату с новым общим "
                "значением (переопределения начала по дням будут сброшены).\n\n"
                "Введите новое начало дня (HH:MM, минуты только :00 или :30), например 09:00",
                reply_markup=admin_reply_keyboard(),
            )
            return
        if field == "close":
            await state.set_state(AdminStates.schedule_close)
            await callback.message.answer(
                "⚠️ <b>Внимание</b>\n\n"
                "Изменение общего последнего слота приведёт все индивидуальные настройки "
                "последнего слота по конкретным дням к одному формату с новым общим "
                "значением (переопределения последнего слота по дням будут сброшены).\n\n"
                "Введите новый последний слот (HH:MM, минуты только :00 или :30), например 18:30",
                reply_markup=admin_reply_keyboard(),
            )
            return
        if field == "workday_end":
            await state.set_state(AdminStates.schedule_workday_end)
            await callback.message.answer(
                "⚠️ <b>Внимание</b>\n\n"
                "Изменение общего конца рабочего дня потребует подтверждения и "
                "сбросит индивидуальные настройки конца рабочего дня по датам.\n\n"
                "Введите новый конец рабочего дня (HH:MM, минуты только :00 или :30), например 20:00",
                reply_markup=admin_reply_keyboard(),
            )
            return
        if field == "step":
            await callback.message.answer(
                "Шаг слотов определяется длительностью услуги в каталоге.",
                reply_markup=admin_reply_keyboard(),
            )
            return

    if action == "ops_sc_gss":
        await state.set_state(AdminStates.schedule_step)
        await callback.message.answer(
            "⚠️ <b>Внимание</b>\n\n"
            "Изменение общего шага слотов приведёт все индивидуальные шаги слотов "
            "для конкретных дней к одному формату с новым общим значением "
            "(отдельные переопределения шага по дням будут сброшены).\n\n"
            "Введите шаг слотов (мин, кратно 30, например 30 или 60):",
            reply_markup=admin_reply_keyboard(),
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
            text = (
                "<b>Каталог услуг</b>\n\n"
                "Используется для записи (выбор услуги, длительность слотов)."
            )
        else:
            text = "<b>Каталог услуг</b>\n\nПока пусто."
        await callback.message.answer(text, reply_markup=b.as_markup())
        return

    if action == "ops_pl":
        items = admin_ops_uc.list_price_items()
        b = InlineKeyboardBuilder()
        if items:
            for item in items[:40]:
                b.button(
                    text=_price_line(item)[:60],
                    callback_data=build_admin_ops_price_open(item.item_id),
                )
        b.button(text="➕ Добавить", callback_data=build_admin_ops_price_add())
        b.button(text="« Меню", callback_data=build_admin_home())
        b.adjust(1)
        intro = (
            "<b>Прайс (для клиентов)</b>\n\n"
            "Здесь строки, которые видят в разделе «💸 Прайс» в клиентском меню."
        )
        if not items:
            intro += "\n\nПока нет позиций."
        await callback.message.answer(intro, reply_markup=b.as_markup())
        return

    if action == "ops_pl_add":
        await state.set_state(AdminStates.price_add)
        await callback.message.answer(
            "Введите данные в формате: Название, цена\n"
            "Пример: Маникюр, 2000",
            reply_markup=admin_reply_keyboard(),
        )
        return

    if action == "ops_pl_o" and parts:
        item_id = parts[0]
        try:
            item = admin_ops_uc.get_price_item_or_raise(item_id)
        except AppError as e:
            await callback.message.answer(
                error_to_user_message(e),
                reply_markup=admin_reply_keyboard(),
            )
            return
        b = InlineKeyboardBuilder()
        b.button(text="Изменить", callback_data=build_admin_ops_price_edit(item_id))
        b.button(text="Удалить", callback_data=build_admin_ops_price_delete(item_id))
        if item.is_active:
            b.button(text="Деактивировать", callback_data=build_admin_ops_price_deactivate(item_id))
        else:
            b.button(text="Активировать", callback_data=build_admin_ops_price_activate(item_id))
        b.button(text="« К прайсу", callback_data=build_admin_ops_price_list())
        b.adjust(1)
        body = item.display_text.replace("<", "&lt;").replace(">", "&gt;")
        await callback.message.answer(
            f"<b>Позиция прайса</b>\n\n{body}",
            reply_markup=b.as_markup(),
        )
        return

    if action == "ops_pl_off" and parts:
        item_id = parts[0]
        try:
            admin_ops_uc.deactivate_price_item(item_id)
        except AppError as e:
            await callback.message.answer(
                error_to_user_message(e),
                reply_markup=admin_reply_keyboard(),
            )
            return
        await callback.message.answer(
            "Позиция скрыта для клиентов (не удалена).",
            reply_markup=admin_reply_keyboard(),
        )
        return

    if action == "ops_pl_on" and parts:
        item_id = parts[0]
        try:
            admin_ops_uc.activate_price_item(item_id)
        except AppError as e:
            await callback.message.answer(
                error_to_user_message(e),
                reply_markup=admin_reply_keyboard(),
            )
            return
        await callback.message.answer(
            "Позиция снова активна для клиентов.",
            reply_markup=admin_reply_keyboard(),
        )
        return

    if action == "ops_pl_ed" and parts:
        item_id = parts[0]
        try:
            admin_ops_uc.get_price_item_or_raise(item_id)
        except AppError as e:
            await callback.message.answer(
                error_to_user_message(e),
                reply_markup=admin_reply_keyboard(),
            )
            return
        await state.set_state(AdminStates.price_edit)
        await state.update_data(ops_price_id=item_id)
        await callback.message.answer(
            "Введите данные в формате: Название, цена\n"
            "Пример: Маникюр, 2000",
            reply_markup=admin_reply_keyboard(),
        )
        return

    if action == "ops_pl_del" and parts:
        item_id = parts[0]
        try:
            admin_ops_uc.delete_price_item(item_id)
        except AppError as e:
            await callback.message.answer(
                error_to_user_message(e),
                reply_markup=admin_reply_keyboard(),
            )
            return
        await callback.message.answer(
            "Позиция прайса удалена.",
            reply_markup=admin_reply_keyboard(),
        )
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
            await callback.message.answer(
                error_to_user_message(e),
                reply_markup=admin_reply_keyboard(),
            )
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
            await callback.message.answer(
                error_to_user_message(e),
                reply_markup=admin_reply_keyboard(),
            )
            return
        await callback.message.answer(
            "Услуга деактивирована.",
            reply_markup=admin_reply_keyboard(),
        )
        return

    if action == "ops_sv_tg" and parts:
        service_id = parts[0]
        try:
            item = admin_ops_uc.get_service_or_raise(service_id)
            updated = admin_ops_uc.update_service(service_id, is_active=not item.is_active)
        except AppError as e:
            await callback.message.answer(
                error_to_user_message(e),
                reply_markup=admin_reply_keyboard(),
            )
            return
        await callback.message.answer(
            f"Статус обновлён: {'активна' if updated.is_active else 'деактивирована'}.",
            reply_markup=admin_reply_keyboard(),
        )
        return

    if action == "ops_sv_del" and parts:
        service_id = parts[0]
        try:
            admin_ops_uc.delete_service(service_id)
        except AppError as e:
            await callback.message.answer(
                error_to_user_message(e),
                reply_markup=admin_reply_keyboard(),
            )
            return
        await callback.message.answer(
            "Услуга удалена.",
            reply_markup=admin_reply_keyboard(),
        )
        return

    if action == "ops_sv_ed" and len(parts) >= 2:
        service_id = parts[0]
        field = parts[1]
        if field == "name":
            await state.set_state(AdminStates.service_edit_name)
            await state.update_data(ops_service_id=service_id)
            await callback.message.answer(
                "Введите новое название услуги:",
                reply_markup=admin_reply_keyboard(),
            )
            return
        if field == "duration":
            await state.set_state(AdminStates.service_edit_duration)
            await state.update_data(ops_service_id=service_id)
            await callback.message.answer(
                "Введите новую длительность в минутах (кратно 30, например 60):",
                reply_markup=admin_reply_keyboard(),
            )
            return
        if field == "price":
            await state.set_state(AdminStates.service_edit_price)
            await state.update_data(ops_service_id=service_id)
            await callback.message.answer(
                "Введите новую цену (текстом):",
                reply_markup=admin_reply_keyboard(),
            )
            return
        await callback.message.answer(
            "Неизвестное поле редактирования услуги.",
            reply_markup=admin_reply_keyboard(),
        )
        return

    if action == "ops_bl":
        items = admin_ops_uc.list_blacklist()
        b = InlineKeyboardBuilder()
        active = [x for x in items if x.is_active]
        if active:
            for entry in active[:30]:
                b.button(
                    text=f"❌ {_blacklist_line(entry)}"[:60],
                    callback_data=build_admin_ops_blacklist_open(entry.entry_id),
                )
        b.button(text="➕ Добавить", callback_data=build_admin_ops_blacklist_add())
        b.button(text="« Меню", callback_data=build_admin_home())
        b.adjust(1)
        text = (
            "<b>Чёрный список 🚫</b>\n\n"
            if active
            else "Чёрный список пуст.\nНет ограниченных клиентов."
        )
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
            await callback.message.answer(
                error_to_user_message(e),
                reply_markup=admin_reply_keyboard(),
            )
            return
        await callback.message.answer(
            "Запись в blacklist деактивирована.",
            reply_markup=admin_reply_keyboard(),
        )
        return

    if action == "ops_bl_o" and parts:
        entry_id = parts[0]
        entry = next((x for x in admin_ops_uc.list_blacklist() if x.entry_id == entry_id), None)
        if entry is None:
            await callback.message.answer(
                "Запись blacklist не найдена.",
                reply_markup=admin_reply_keyboard(),
            )
            return
        await _send_blacklist_entry_card(callback.message, entry)
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
            _ADMIN_CALENDAR_TITLE_AND_LEGEND,
            reply_markup=_month_keyboard(shift, admin_ops_uc.is_day_closed, _is_short_day, appointment_uc, admin_ops_uc).as_markup(),
        )
        return

    if action == "md" and parts:
        ymd = parts[0]
        try:
            items = appointment_uc.admin_list_for_date(ymd)
        except AppError as e:
            await callback.message.answer(
                error_to_user_message(e),
                reply_markup=admin_reply_keyboard(),
            )
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
                if admin_ops_uc.is_slot_disabled(ymd, hhmm):
                    b.button(
                        text=f"⛔{label}",
                        callback_data=build_admin_slot_open(ymd, hhmm),
                    )
                    continue
                b.button(
                    text=f"{label} · свободно",
                    callback_data=build_admin_slot_open(ymd, hhmm),
                )
            else:
                b.button(
                    text=f"{label} 🔒",
                    callback_data=build_admin_open(ap.appointment_id),
                )
        if is_closed:
            b.button(text="✅ Открыть день", callback_data=build_admin_day_toggle(ymd, True))
        else:
            b.button(text="⛔ Закрыть день", callback_data=build_admin_day_toggle(ymd, False))
            b.button(text="Изм. начало дня", callback_data=build_admin_day_edit(ymd, "open"))
            b.button(text="Изм. последний слот", callback_data=build_admin_day_edit(ymd, "close"))
            b.button(
                text="Изм. конец рабочего дня",
                callback_data=build_admin_day_edit(ymd, "workday_end"),
            )
        b.button(text="« К календарю", callback_data=build_admin_month(0))
        b.button(text="« Меню", callback_data=build_admin_home())
        b.adjust(1)
        await _safe_edit_or_answer(
            callback,
            (
                f"Записи на {_fmt_ymd(ymd)}: {'день закрыт' if is_closed else 'выберите время'}\n"
                f"Диапазон стартов: {_fmt_hhmm(day_sched.open_time_hhmm)}-{_fmt_hhmm(day_sched.close_time_hhmm)}\n"
                f"Конец рабочего дня: {_fmt_hhmm(day_sched.workday_end_time_hhmm)}, "
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
            await callback.message.answer(
                error_to_user_message(e),
                reply_markup=admin_reply_keyboard(),
            )
            return
        await callback.message.answer(
            "День обновлён.",
            reply_markup=admin_reply_keyboard(),
        )
        return

    if action == "ops_day_ed" and len(parts) >= 2:
        ymd = parts[0]
        field = parts[1]
        await state.update_data(day_ymd=ymd)
        if field == "open":
            await state.set_state(AdminStates.day_schedule_open)
            await callback.message.answer(
                "Введите начало дня для этой даты (HH:MM, минуты только :00 или :30):",
                reply_markup=admin_reply_keyboard(),
            )
            return
        if field == "close":
            await state.set_state(AdminStates.day_schedule_close)
            await callback.message.answer(
                "Введите последний слот для этой даты (HH:MM, минуты только :00 или :30):",
                reply_markup=admin_reply_keyboard(),
            )
            return
        if field == "workday_end":
            await state.set_state(AdminStates.day_schedule_workday_end)
            await callback.message.answer(
                "Введите конец рабочего дня для этой даты (HH:MM, минуты только :00 или :30):",
                reply_markup=admin_reply_keyboard(),
            )
            return
        if field == "step":
            await callback.message.answer(
                "Изменение шага слотов для конкретного дня отключено.",
                reply_markup=admin_reply_keyboard(),
            )
            return

    if action == "ts" and len(parts) >= 2:
        # legacy callback from old keyboards: redirect to slot screen.
        ymd = parts[0]
        hhmm = parts[1]
        action = "sl"
        parts = [ymd, hhmm]
    if action == "sl" and len(parts) >= 2:
        ymd = parts[0]
        hhmm = parts[1]
        try:
            items = appointment_uc.admin_list_for_date(ymd)
        except AppError as e:
            await callback.message.answer(
                error_to_user_message(e),
                reply_markup=admin_reply_keyboard(),
            )
            return
        ap = _slot_is_occupied_for_items(items, admin_ops_uc, hhmm)
        if ap is not None:
            await _send_appointment_card(callback.message, ap, settings, admin_ops_uc)
            return
        disabled = admin_ops_uc.is_slot_disabled(ymd, hhmm)
        b = InlineKeyboardBuilder()
        if disabled:
            b.button(text="✅ Включить", callback_data=build_admin_slot_toggle(ymd, hhmm, True))
        else:
            b.button(text="⛔ Отключить", callback_data=build_admin_slot_toggle(ymd, hhmm, False))
        b.button(text="⬅️ Назад", callback_data=build_admin_month_date(ymd))
        b.button(text="🏠 Меню", callback_data=build_admin_home())
        b.adjust(1)
        await callback.message.answer(
            f"Слот {_fmt_ymd(ymd)} {hhmm[:2]}:{hhmm[2:]} — "
            f"{'отключён' if disabled else 'активен'}",
            reply_markup=b.as_markup(),
        )
        return
    if action == "slt" and len(parts) >= 3:
        ymd = parts[0]
        hhmm = parts[1]
        enable = parts[2] == "1"
        try:
            items = appointment_uc.admin_list_for_date(ymd)
        except AppError as e:
            await callback.message.answer(
                error_to_user_message(e),
                reply_markup=admin_reply_keyboard(),
            )
            return
        ap = _slot_is_occupied_for_items(items, admin_ops_uc, hhmm)
        if ap is not None:
            await callback.message.answer(
                "Нельзя изменить статус занятого слота.",
                reply_markup=admin_reply_keyboard(),
            )
            return
        try:
            admin_ops_uc.set_slot_disabled(ymd, hhmm, is_disabled=not enable)
        except AppError as e:
            await callback.message.answer(
                error_to_user_message(e),
                reply_markup=admin_reply_keyboard(),
            )
            return
        await callback.message.answer(
            "Слот включён." if enable else "Слот отключён.",
            reply_markup=admin_reply_keyboard(),
        )
        return

    if action == "bk":
        if not parts:
            await callback.message.answer(
                "Команда устарела. Откройте /admin заново.",
                reply_markup=admin_reply_keyboard(),
            )
            return
        if parts == ["hm"]:
            await callback.message.answer(
                "Панель управления ✨\nВыберите нужный раздел.",
                reply_markup=_home_keyboard().as_markup(),
            )
        elif parts == ["td"]:
            await callback.message.answer(
                _ADMIN_CALENDAR_TITLE_AND_LEGEND,
                reply_markup=_month_keyboard(0, admin_ops_uc.is_day_closed, _is_short_day, appointment_uc, admin_ops_uc).as_markup(),
            )
        elif parts == ["cn"]:
            items = appointment_uc.admin_list_cancelled()
            await _send_appointment_list(
                callback.message,
                items,
                "Отменённые записи 🚫\nНиже отображается история отмен.",
                include_cancelled_actions=True,
                page=0,
            )
        elif parts[0] == "d" and len(parts) >= 2:
            ymd = parts[1]
            await callback.message.answer(
                f"Откройте дату {_fmt_ymd(ymd)} через раздел «Записи».",
                reply_markup=_month_keyboard(0, admin_ops_uc.is_day_closed, _is_short_day, appointment_uc, admin_ops_uc).as_markup(),
            )
        return

    if action == "p" and parts:
        ap_id = parts[0]
        try:
            ap = appointment_uc.get_appointment_or_raise(ap_id)
        except AppError as e:
            await callback.message.answer(
                error_to_user_message(e),
                reply_markup=admin_reply_keyboard(),
            )
            return
        if ap.status == AppointmentStatus.CANCELLED:
            await _send_cancelled_appointment_card(callback.message, ap, settings, admin_ops_uc)
        else:
            await _send_appointment_card(callback.message, ap, settings, admin_ops_uc)
        return

    if action == "x" and parts:
        ap_id = parts[0]
        try:
            _, already = appointment_uc.admin_cancel(ap_id)
        except AppError as e:
            await callback.message.answer(
                error_to_user_message(e),
                reply_markup=admin_reply_keyboard(),
            )
            return
        msg = "Запись уже была отменена." if already else "Запись отменена."
        await callback.message.answer(msg, reply_markup=admin_reply_keyboard())
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
                await callback.message.answer(
                    "Клиент уже в активном blacklist.",
                    reply_markup=admin_reply_keyboard(),
                )
                return
            admin_ops_uc.add_blacklist_entry(
                user_id=int(ap.user_id) if ap.user_id is not None else None,
                phone_e164=ap.phone_e164 or None,
                reason=None,
            )
        except AppError as e:
            await callback.message.answer(
                error_to_user_message(e),
                reply_markup=admin_reply_keyboard(),
            )
            return
        await callback.message.answer(
            "Клиент добавлен в blacklist.",
            reply_markup=admin_reply_keyboard(),
        )
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
            "Отменённые записи 🚫\nНиже отображается история отмен.",
            include_cancelled_actions=True,
            page=page,
        )
        return

    if action == "acp" and parts:
        try:
            page = max(int(parts[0]), 0)
        except Exception:
            page = 0
        items = appointment_uc.admin_list_active()
        await _send_appointment_list(
            callback.message,
            items,
            "Текущие записи ✨\nВот все подтверждённые записи.",
            active_list=True,
            page=page,
        )
        return

    if action == "e" and parts:
        ap_id = parts[0]
        try:
            ap = appointment_uc.get_appointment_or_raise(ap_id)
        except AppError as e:
            await callback.message.answer(
                error_to_user_message(e),
                reply_markup=admin_reply_keyboard(),
            )
            return
        dates = _available_dates(settings, admin_ops_uc)
        holiday_dates, busy_dates = _calendar_holiday_and_fully_busy_sets(
            booking_uc, admin_ops_uc, settings, dates, ap.service_id
        )
        await callback.message.answer(
            _ADMIN_MOVE_DATE_SCREEN,
            reply_markup=admin_move_date_keyboard(
                ap_id, dates, holiday_dates, busy_dates
            ),
        )
        return

    if action == "mv" and parts:
        ap_id = parts[0]
        try:
            ap = appointment_uc.get_appointment_or_raise(ap_id)
        except AppError as e:
            await callback.message.answer(
                error_to_user_message(e),
                reply_markup=admin_reply_keyboard(),
            )
            return
        dates = _available_dates(settings, admin_ops_uc)
        holiday_dates, busy_dates = _calendar_holiday_and_fully_busy_sets(
            booking_uc, admin_ops_uc, settings, dates, ap.service_id
        )
        await callback.message.answer(
            _ADMIN_MOVE_DATE_SCREEN,
            reply_markup=admin_move_date_keyboard(
                ap_id, dates, holiday_dates, busy_dates
            ),
        )
        return

    if action == "mvd" and len(parts) >= 2:
        ap_id = parts[0]
        ymd = parts[1]
        try:
            ap = appointment_uc.get_appointment_or_raise(ap_id)
            items = appointment_uc.admin_list_for_date(ymd)
        except AppError as e:
            await callback.message.answer(
                error_to_user_message(e),
                reply_markup=admin_reply_keyboard(),
            )
            return
        sched = admin_ops_uc.get_effective_schedule_for_date(ymd)
        slots = _slots_for_schedule(
            sched.open_time_hhmm,
            sched.close_time_hhmm,
            sched.slot_minutes,
        )
        b = InlineKeyboardBuilder()
        free_slots: list[str] = []
        for hhmm in slots:
            if admin_ops_uc.is_slot_disabled(ymd, hhmm):
                b.button(
                    text=f"⛔{hhmm[:2]}:{hhmm[2:]}",
                    callback_data="a1|noop",
                )
                continue
            if _slot_is_occupied_for_items(
                items,
                admin_ops_uc,
                hhmm,
                date_yyyymmdd=ymd,
                exclude_appointment_id=ap.appointment_id,
                requested_service_id=ap.service_id,
                workday_end_hhmm=sched.workday_end_time_hhmm,
            ):
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
            await callback.message.answer(
                error_to_user_message(e),
                reply_markup=admin_reply_keyboard(),
            )
            return
        await callback.message.answer(
            "Запись перенесена.",
            reply_markup=admin_reply_keyboard(),
        )
        return

    if action == "s" and len(parts) >= 2:
        ap_id = parts[0]
        try:
            idx = int(parts[1])
        except ValueError:
            await callback.message.answer(
                "Некорректные данные.",
                reply_markup=admin_reply_keyboard(),
            )
            return
        if idx < 0 or idx >= len(settings.services):
            await callback.message.answer(
                "Некорректная услуга.",
                reply_markup=admin_reply_keyboard(),
            )
            return
        svc = settings.services[idx]
        try:
            ap = appointment_uc.admin_update(ap_id, service_id=svc)
        except AppError as e:
            await callback.message.answer(
                error_to_user_message(e),
                reply_markup=admin_reply_keyboard(),
            )
            return
        await callback.message.answer(
            "Услуга обновлена.",
            reply_markup=admin_reply_keyboard(),
        )
        await _send_appointment_card(callback.message, ap, settings, admin_ops_uc)
        return

    if action == "dt" and len(parts) >= 2:
        ap_id = parts[0]
        ymd = parts[1]
        try:
            ap0 = appointment_uc.get_appointment_or_raise(ap_id)
        except AppError as e:
            await callback.message.answer(
                error_to_user_message(e),
                reply_markup=admin_reply_keyboard(),
            )
            return
        old = ap0.start_datetime_utc or ""
        time_part = "1000"
        if "T" in old and len(old.split("T", 1)[1]) >= 4:
            time_part = old.split("T", 1)[1][:4]
        new_start = f"{ymd}T{time_part}"
        try:
            ap = appointment_uc.admin_update(ap_id, start_datetime_utc=new_start)
        except AppError as e:
            await callback.message.answer(
                error_to_user_message(e),
                reply_markup=admin_reply_keyboard(),
            )
            return
        await callback.message.answer(
            "Дата обновлена (время сохранено).",
            reply_markup=admin_reply_keyboard(),
        )
        await _send_appointment_card(callback.message, ap, settings, admin_ops_uc)
        return

    if action == "tm" and len(parts) >= 2:
        ap_id = parts[0]
        hhmm = parts[1]
        try:
            ap0 = appointment_uc.get_appointment_or_raise(ap_id)
        except AppError as e:
            await callback.message.answer(
                error_to_user_message(e),
                reply_markup=admin_reply_keyboard(),
            )
            return
        old = ap0.start_datetime_utc or ""
        if "T" not in old or len(old) < 9:
            await callback.message.answer(
                "Некорректная исходная дата записи.",
                reply_markup=admin_reply_keyboard(),
            )
            return
        d_part = old.split("T", 1)[0]
        new_start = f"{d_part}T{hhmm}"
        try:
            ap = appointment_uc.admin_update(ap_id, start_datetime_utc=new_start)
        except AppError as e:
            await callback.message.answer(
                error_to_user_message(e),
                reply_markup=admin_reply_keyboard(),
            )
            return
        await callback.message.answer(
            "Время обновлено.",
            reply_markup=admin_reply_keyboard(),
        )
        await _send_appointment_card(callback.message, ap, settings, admin_ops_uc)
        return

    if action == "nm" and parts:
        await state.set_state(AdminStates.edit_name)
        await state.update_data(admin_aid=parts[0])
        await callback.message.answer(
            "Введите новое имя (одним сообщением):",
            reply_markup=admin_reply_keyboard(),
        )
        return

    if action == "ph" and parts:
        await state.set_state(AdminStates.edit_phone)
        await state.update_data(admin_aid=parts[0])
        await callback.message.answer(
            "Введите новый телефон:",
            reply_markup=admin_reply_keyboard(),
        )
        return

    await callback.message.answer(
        "Команда устарела. Откройте /admin.",
        reply_markup=admin_reply_keyboard(),
    )


async def _send_appointment_list(
    message: Message,
    items: list[Appointment],
    title: str,
    include_cancelled_actions: bool = False,
    active_list: bool = False,
    page: int = 0,
) -> None:
    if not items:
        if active_list:
            empty_text = (
                "Текущие записи ✨\n"
                "Вот все подтверждённые записи. (0)\n"
                "Страница 1/1\n\n"
                "🛠 — создано админом"
            )
            await message.answer(empty_text, reply_markup=admin_reply_keyboard())
        else:
            await message.answer(
                f"{title}: записей нет.",
                reply_markup=admin_reply_keyboard(),
            )
        return
    page_size = 10
    total_pages = max((len(items) - 1) // page_size + 1, 1)
    page = min(max(page, 0), total_pages - 1)
    start = page * page_size
    stop = start + page_size
    page_items = items[start:stop]

    header = f"<b>{title}</b> ({len(items)})\nСтраница {page + 1}/{total_pages}\n"
    if include_cancelled_actions:
        await message.answer(
            header.strip(),
            reply_markup=admin_reply_keyboard(),
        )
    elif active_list:
        intro = (
            "Текущие записи ✨\n"
            f"Вот все подтверждённые записи. ({len(items)})\n"
            f"Страница {page + 1}/{total_pages}\n\n"
            "🛠 — создано админом"
        )
        await message.answer(intro, reply_markup=admin_reply_keyboard())
    else:
        def _row(a: Appointment) -> str:
            return f"• {format_slot_utc_for_user(a.start_datetime_utc)} — {a.service_id} — {a.customer_name} — {a.phone_e164}"
        text = header + "\n".join(_row(a) for a in page_items)
        await message.answer(text, reply_markup=admin_reply_keyboard())

    b = InlineKeyboardBuilder()
    for ap in page_items:
        if include_cancelled_actions:
            line = (
                f"{format_slot_utc_for_user(ap.start_datetime_utc)} | "
                f"{ap.service_id} | {ap.customer_name} | {ap.phone_e164}"
            )
            b.button(
                text=line[:64],
                callback_data=build_admin_open(ap.appointment_id),
            )
        elif active_list:
            b.button(
                text=_active_list_button_text(ap),
                callback_data=build_admin_open(ap.appointment_id),
            )
        else:
            b.button(text=f"Открыть · {_btn_line(ap)}", callback_data=build_admin_open(ap.appointment_id))
    if total_pages > 1:
        prev_cb = build_admin_active_page(page - 1) if active_list else build_admin_cancelled_page(page - 1)
        next_cb = build_admin_active_page(page + 1) if active_list else build_admin_cancelled_page(page + 1)
        if page > 0:
            b.button(text="« Пред.", callback_data=prev_cb)
        if page < total_pages - 1:
            b.button(text="След. »", callback_data=next_cb)
    b.button(text="« Меню", callback_data=build_admin_home())
    b.adjust(1)
    await message.answer(("Записи:" if include_cancelled_actions else "Открыть запись:"), reply_markup=b.as_markup())


async def _send_appointment_card(
    message: Message,
    ap: Appointment,
    settings: Settings,
    admin_ops_uc: AdminOpsUseCases | None = None,
) -> None:
    b = InlineKeyboardBuilder()
    if ap.status != AppointmentStatus.CANCELLED:
        b.button(text="❌ Отменить", callback_data=build_admin_cancel_appt(ap.appointment_id))
        b.button(text="🔁 Перенести", callback_data=build_admin_move_start(ap.appointment_id))
    b.button(text="« Меню", callback_data=build_admin_home())
    b.adjust(1)
    await message.answer(_card_text(ap, settings, admin_ops_uc), reply_markup=b.as_markup())


async def _send_cancelled_appointment_card(
    message: Message,
    ap: Appointment,
    settings: Settings,
    admin_ops_uc: AdminOpsUseCases | None = None,
) -> None:
    b = InlineKeyboardBuilder()
    b.button(text="🚫 В blacklist", callback_data=build_admin_blacklist_from_appointment(ap.appointment_id))
    b.button(text="« К отменённым", callback_data=build_admin_cancelled())
    b.adjust(1)
    await message.answer(_card_text(ap, settings, admin_ops_uc), reply_markup=b.as_markup())


def _blacklist_card_text(entry: BlacklistEntry) -> str:
    who = f"user_id={entry.user_id}" if entry.user_id is not None else "user_id=—"
    phone = entry.phone_e164 or "—"
    reason = entry.reason or "—"
    status = "active" if entry.is_active else "inactive"
    return (
        "<b>Blacklist entry</b>\n\n"
        f"Статус: {status}\n"
        f"👤 {who}\n"
        f"📞 {phone}\n"
        f"📝 {reason}"
    )


async def _send_blacklist_entry_card(
    message: Message,
    entry: BlacklistEntry,
) -> None:
    b = InlineKeyboardBuilder()
    if entry.is_active:
        b.button(text="✅ Разблокировать", callback_data=build_admin_ops_blacklist_deactivate(entry.entry_id))
    b.button(text="« К blacklist", callback_data=build_admin_ops_blacklist())
    b.adjust(1)
    await message.answer(_blacklist_card_text(entry), reply_markup=b.as_markup())


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
        f"Поиск: {q}" if q else "Поиск",
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
        await message.answer(
            error_to_user_message(e) + _ADMIN_FSM_TIME_HINT,
            reply_markup=admin_reply_keyboard(),
        )
        return
    await state.clear()
    await message.answer(
        "Начало дня обновлено.\n"
        "Индивидуальные времена начала по дням приведены к общему формату.",
        reply_markup=admin_reply_keyboard(),
    )


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
        await message.answer(
            error_to_user_message(e) + _ADMIN_FSM_TIME_HINT,
            reply_markup=admin_reply_keyboard(),
        )
        return
    await state.clear()
    await message.answer(
        "Последний слот обновлён.\n"
        "Индивидуальные значения последнего слота по дням приведены к общему формату.",
        reply_markup=admin_reply_keyboard(),
    )


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
        admin_ops_uc.update_schedule_slot_unify_day_overrides(step)
    except ValueError:
        await message.answer(
            "Введите целое число минут." + _ADMIN_FSM_TIME_HINT,
            reply_markup=admin_reply_keyboard(),
        )
        return
    except AppError as e:
        await message.answer(
            error_to_user_message(e) + _ADMIN_FSM_TIME_HINT,
            reply_markup=admin_reply_keyboard(),
        )
        return
    await state.clear()
    await message.answer(
        "Общий шаг слотов обновлён: "
        f"{_fmt_duration_minutes(step)}.\n"
        "Индивидуальные шаги по дням приведены к этому же формату.",
        reply_markup=admin_reply_keyboard(),
    )


@router.message(AdminStates.schedule_workday_end, F.text)
async def admin_schedule_set_workday_end(
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
    await state.update_data(pending_workday_end_hhmm=value)
    await state.set_state(AdminStates.schedule_workday_end_confirm)
    await message.answer(
        "Подтвердите изменение общего конца рабочего дня.\n"
        "Это действие сбросит только индивидуальные переопределения конца рабочего дня по датам.\n"
        "Ответьте: ДА",
        reply_markup=admin_reply_keyboard(),
    )


@router.message(AdminStates.schedule_workday_end_confirm, F.text)
async def admin_schedule_set_workday_end_confirm(
    message: Message,
    state: FSMContext,
    settings: Settings,
    admin_ops_uc: AdminOpsUseCases,
) -> None:
    uid = message.from_user.id if message.from_user else None
    if not _is_admin(uid, settings):
        await state.clear()
        return
    answer = (message.text or "").strip().lower()
    if answer != "да":
        await state.clear()
        await message.answer(
            "Изменение отменено.",
            reply_markup=admin_reply_keyboard(),
        )
        return
    data = await state.get_data()
    value = (data.get("pending_workday_end_hhmm") or "").strip()
    try:
        admin_ops_uc.update_schedule(
            workday_end_time_hhmm=value,
            reset_day_workday_end_overrides=True,
        )
    except AppError as e:
        await message.answer(
            error_to_user_message(e) + _ADMIN_FSM_TIME_HINT,
            reply_markup=admin_reply_keyboard(),
        )
        return
    await state.clear()
    await message.answer(
        "Конец рабочего дня обновлён.\n"
        "Индивидуальные переопределения конца рабочего дня по датам сброшены.",
        reply_markup=admin_reply_keyboard(),
    )


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
        await message.answer(
            "Сессия устарела.",
            reply_markup=admin_reply_keyboard(),
        )
        return
    try:
        admin_ops_uc.update_day_schedule(ymd, open_time_hhmm=(message.text or "").strip())
    except AppError as e:
        await message.answer(
            error_to_user_message(e) + _ADMIN_FSM_TIME_HINT,
            reply_markup=admin_reply_keyboard(),
        )
        return
    await state.clear()
    await message.answer(
        "Начало дня обновлено.",
        reply_markup=admin_reply_keyboard(),
    )


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
        await message.answer(
            "Сессия устарела.",
            reply_markup=admin_reply_keyboard(),
        )
        return
    try:
        admin_ops_uc.update_day_schedule(ymd, close_time_hhmm=(message.text or "").strip())
    except AppError as e:
        await message.answer(
            error_to_user_message(e) + _ADMIN_FSM_TIME_HINT,
            reply_markup=admin_reply_keyboard(),
        )
        return
    await state.clear()
    await message.answer(
        "Последний слот обновлён.",
        reply_markup=admin_reply_keyboard(),
    )


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
        await message.answer(
            "Сессия устарела.",
            reply_markup=admin_reply_keyboard(),
        )
        return
    try:
        admin_ops_uc.update_day_schedule(ymd, slot_minutes=int((message.text or "").strip()))
    except ValueError:
        await message.answer(
            "Введите целое число минут." + _ADMIN_FSM_TIME_HINT,
            reply_markup=admin_reply_keyboard(),
        )
        return
    except AppError as e:
        await message.answer(
            error_to_user_message(e) + _ADMIN_FSM_TIME_HINT,
            reply_markup=admin_reply_keyboard(),
        )
        return
    await state.clear()
    await message.answer(
        "Шаг слотов для дня обновлён.",
        reply_markup=admin_reply_keyboard(),
    )


@router.message(AdminStates.day_schedule_workday_end, F.text)
async def admin_day_schedule_set_workday_end(
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
        await message.answer(
            "Сессия устарела.",
            reply_markup=admin_reply_keyboard(),
        )
        return
    try:
        admin_ops_uc.update_day_schedule(ymd, workday_end_time_hhmm=(message.text or "").strip())
    except AppError as e:
        await message.answer(
            error_to_user_message(e) + _ADMIN_FSM_TIME_HINT,
            reply_markup=admin_reply_keyboard(),
        )
        return
    await state.clear()
    await message.answer(
        "Конец рабочего дня для даты обновлён.",
        reply_markup=admin_reply_keyboard(),
    )


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
            reply_markup=admin_reply_keyboard(),
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
            reply_markup=admin_reply_keyboard(),
        )
        return
    name, dur_raw, price = parts[0], parts[1], " | ".join(parts[2:])
    try:
        duration = int(dur_raw)
        admin_ops_uc.add_service(name, duration, price)
    except ValueError:
        await message.answer(
            "Длительность должна быть числом. Пример: Стрижка | 60 | 1500 ₽" + _ADMIN_FSM_TIME_HINT,
            reply_markup=admin_reply_keyboard(),
        )
        return
    except AppError as e:
        await message.answer(
            error_to_user_message(e) + _ADMIN_FSM_TIME_HINT,
            reply_markup=admin_reply_keyboard(),
        )
        return
    await state.clear()
    await message.answer(
        "Услуга добавлена.",
        reply_markup=admin_reply_keyboard(),
    )


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
        await message.answer(
            "Сессия устарела.",
            reply_markup=admin_reply_keyboard(),
        )
        return
    try:
        admin_ops_uc.update_service(service_id, name=(message.text or "").strip())
    except AppError as e:
        await message.answer(
            error_to_user_message(e),
            reply_markup=admin_reply_keyboard(),
        )
        return
    await state.clear()
    await message.answer(
        "Название услуги обновлено.",
        reply_markup=admin_reply_keyboard(),
    )


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
        await message.answer(
            "Сессия устарела.",
            reply_markup=admin_reply_keyboard(),
        )
        return
    try:
        duration = int((message.text or "").strip())
        admin_ops_uc.update_service(service_id, duration_minutes=duration)
    except ValueError:
        await message.answer(
            "Введите целое число минут." + _ADMIN_FSM_TIME_HINT,
            reply_markup=admin_reply_keyboard(),
        )
        return
    except AppError as e:
        await message.answer(
            error_to_user_message(e) + _ADMIN_FSM_TIME_HINT,
            reply_markup=admin_reply_keyboard(),
        )
        return
    await state.clear()
    await message.answer(
        "Длительность услуги обновлена.",
        reply_markup=admin_reply_keyboard(),
    )


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
        await message.answer(
            "Сессия устарела.",
            reply_markup=admin_reply_keyboard(),
        )
        return
    try:
        admin_ops_uc.update_service(service_id, price_text=(message.text or "").strip())
    except AppError as e:
        await message.answer(
            error_to_user_message(e),
            reply_markup=admin_reply_keyboard(),
        )
        return
    await state.clear()
    await message.answer(
        "Цена услуги обновлена.",
        reply_markup=admin_reply_keyboard(),
    )


@router.message(AdminStates.price_add, F.text)
async def admin_price_add(
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
    try:
        admin_ops_uc.add_price_item(raw)
    except AppError as e:
        await message.answer(
            error_to_user_message(e) + _ADMIN_FSM_TIME_HINT,
            reply_markup=admin_reply_keyboard(),
        )
        return
    await state.clear()
    await message.answer(
        "Позиция прайса добавлена.",
        reply_markup=admin_reply_keyboard(),
    )


@router.message(AdminStates.price_edit, F.text)
async def admin_price_edit(
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
    item_id = str(data.get("ops_price_id") or "")
    if not item_id:
        await state.clear()
        await message.answer(
            "Сессия устарела.",
            reply_markup=admin_reply_keyboard(),
        )
        return
    raw = (message.text or "").strip()
    try:
        admin_ops_uc.update_price_item_text(item_id, raw)
    except AppError as e:
        await message.answer(
            error_to_user_message(e) + _ADMIN_FSM_TIME_HINT,
            reply_markup=admin_reply_keyboard(),
        )
        return
    await state.clear()
    await message.answer(
        "Позиция прайса обновлена.",
        reply_markup=admin_reply_keyboard(),
    )


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
            reply_markup=admin_reply_keyboard(),
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
            reply_markup=admin_reply_keyboard(),
        )
        return

    try:
        admin_ops_uc.add_blacklist_entry(user_id=user_id, phone_e164=phone, reason=reason)
    except AppError as e:
        await message.answer(
            error_to_user_message(e),
            reply_markup=admin_reply_keyboard(),
        )
        return
    await state.clear()
    await message.answer(
        "Запись в blacklist добавлена.",
        reply_markup=admin_reply_keyboard(),
    )


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
        await message.answer(
            error_to_user_message(e),
            reply_markup=admin_reply_keyboard(),
        )
        return
    await state.clear()
    await message.answer(
        "Адрес обновлён.",
        reply_markup=admin_reply_keyboard(),
    )


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
        await message.answer(
            error_to_user_message(e),
            reply_markup=admin_reply_keyboard(),
        )
        return
    await state.clear()
    await message.answer(
        "Контакты обновлены.",
        reply_markup=admin_reply_keyboard(),
    )


@router.message(AdminStates.edit_name, F.text)
async def admin_save_name(
    message: Message,
    state: FSMContext,
    settings: Settings,
    appointment_uc: AppointmentUseCases,
    admin_ops_uc: AdminOpsUseCases,
) -> None:
    uid = message.from_user.id if message.from_user else None
    if not _is_admin(uid, settings):
        await state.clear()
        return
    data = await state.get_data()
    aid = str(data.get("admin_aid") or "")
    await state.clear()
    if not aid:
        await message.answer(
            "Сессия устарела.",
            reply_markup=admin_reply_keyboard(),
        )
        return
    name = (message.text or "").strip()
    try:
        ap = appointment_uc.admin_update(aid, customer_name=name)
    except AppError as e:
        await message.answer(
            error_to_user_message(e),
            reply_markup=admin_reply_keyboard(),
        )
        return
    await message.answer(
        "Имя сохранено.",
        reply_markup=admin_reply_keyboard(),
    )
    await _send_appointment_card(message, ap, settings, admin_ops_uc)


@router.message(AdminStates.edit_phone, F.text)
async def admin_save_phone(
    message: Message,
    state: FSMContext,
    settings: Settings,
    appointment_uc: AppointmentUseCases,
    admin_ops_uc: AdminOpsUseCases,
) -> None:
    uid = message.from_user.id if message.from_user else None
    if not _is_admin(uid, settings):
        await state.clear()
        return
    data = await state.get_data()
    aid = str(data.get("admin_aid") or "")
    await state.clear()
    if not aid:
        await message.answer(
            "Сессия устарела.",
            reply_markup=admin_reply_keyboard(),
        )
        return
    phone = (message.text or "").strip()
    try:
        ap = appointment_uc.admin_update(aid, phone_e164=phone)
    except AppError as e:
        await message.answer(
            error_to_user_message(e),
            reply_markup=admin_reply_keyboard(),
        )
        return
    await message.answer(
        "Телефон сохранён.",
        reply_markup=admin_reply_keyboard(),
    )
    await _send_appointment_card(message, ap, settings, admin_ops_uc)
