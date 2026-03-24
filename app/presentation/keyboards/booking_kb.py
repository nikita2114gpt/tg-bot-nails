from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.presentation.callback.booking_callbacks import build_callback
from app.presentation.callback.nav_callbacks import build_admin_move_date, build_admin_open


def _fmt_duration_minutes(value: int) -> str:
    h = value // 60
    m = value % 60
    if h > 0 and m > 0:
        return f"{h} ч {m} мин"
    if h > 0:
        return f"{h} ч"
    return f"{m} мин"


def service_keyboard(draft_id: str, services: list[str], service_repo=None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    duration_by_name: dict[str, str] = {}
    if service_repo is not None:
        try:
            for item in service_repo.list_all():
                name = (getattr(item, "name", "") or "").strip()
                if not name:
                    continue
                duration = int(getattr(item, "duration_minutes", 0) or 0)
                if duration > 0:
                    duration_by_name[name] = _fmt_duration_minutes(duration)
        except Exception:
            duration_by_name = {}

    for service in services:
        label = service
        dur = duration_by_name.get(service)
        if dur:
            label = f"{service} — {dur}"
        builder.button(
            text=label,
            callback_data=build_callback("svc", draft_id, service),
        )

    builder.button(
        text="❌ Отмена",
        callback_data=build_callback("cancel", draft_id),
    )

    builder.adjust(1)
    return builder.as_markup()


def date_keyboard(
    draft_id: str,
    dates: list[str],
    closed_dates: set[str] | None = None,
    fully_busy_dates: set[str] | None = None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    closed_dates = closed_dates or set()
    fully_busy_dates = fully_busy_dates or set()
    norm_dates = [d for d in dates if len(d) == 8 and d.isdigit()]
    if not norm_dates:
        norm_dates = dates
    month_label = ""
    if norm_dates and len(norm_dates[0]) == 8 and norm_dates[0].isdigit():
        month_label = f"{norm_dates[0][6:8]}.{norm_dates[0][4:6]} - {norm_dates[-1][6:8]}.{norm_dates[-1][4:6]}"
    else:
        month_label = "Календарь"
    builder.button(text=month_label, callback_data=build_callback("date", draft_id, "x_hd"))
    for wd in ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"):
        builder.button(text=wd, callback_data=build_callback("date", draft_id, "x_hd"))
    leading_pad = 0
    if norm_dates and len(norm_dates[0]) == 8 and norm_dates[0].isdigit():
        import datetime as _dt

        leading_pad = _dt.datetime.strptime(norm_dates[0], "%Y%m%d").weekday()
    for i in range(leading_pad):
        # Placeholder only for first-week alignment; this is not a real date.
        builder.button(text="·", callback_data=build_callback("date", draft_id, f"x_pad{i}"))
    for date_value in norm_dates:
        label = date_value[6:8] if len(date_value) == 8 and date_value.isdigit() else date_value
        callback_payload = date_value
        if date_value in closed_dates:
            label = f"🚫{label}"
            callback_payload = f"x_{date_value}"
        elif date_value in fully_busy_dates:
            label = f"🔒{label}"
            callback_payload = f"x_{date_value}"
        builder.button(text=label, callback_data=build_callback("date", draft_id, callback_payload))

    builder.button(
        text="⬅️ Назад",
        callback_data=build_callback("bk", draft_id),
    )
    builder.button(
        text="❌ Отмена",
        callback_data=build_callback("cancel", draft_id),
    )

    grid_cells = len(norm_dates) + leading_pad
    rows = grid_cells // 7
    rem = grid_cells % 7
    adjust_pattern: list[int] = []
    adjust_pattern.extend([1, 7])
    if rows > 0:
        adjust_pattern.extend([7] * rows)
    if rem:
        adjust_pattern.append(rem)
    adjust_pattern.extend([1, 1])
    builder.adjust(*adjust_pattern)
    return builder.as_markup()


def admin_move_date_keyboard(
    ap_id: str,
    dates: list[str],
    closed_dates: set[str] | None = None,
    fully_busy_dates: set[str] | None = None,
) -> InlineKeyboardMarkup:
    """Сетка дат как у клиента; callback — перенос записи (a1|mvd|…)."""
    NOOP = "a1|noop"
    builder = InlineKeyboardBuilder()
    closed_dates = closed_dates or set()
    fully_busy_dates = fully_busy_dates or set()
    norm_dates = [d for d in dates if len(d) == 8 and d.isdigit()]
    if not norm_dates:
        norm_dates = dates
    month_label = ""
    if norm_dates and len(norm_dates[0]) == 8 and norm_dates[0].isdigit():
        month_label = f"{norm_dates[0][6:8]}.{norm_dates[0][4:6]} - {norm_dates[-1][6:8]}.{norm_dates[-1][4:6]}"
    else:
        month_label = "Календарь"
    builder.button(text=month_label, callback_data=NOOP)
    for wd in ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"):
        builder.button(text=wd, callback_data=NOOP)
    leading_pad = 0
    if norm_dates and len(norm_dates[0]) == 8 and norm_dates[0].isdigit():
        import datetime as _dt

        leading_pad = _dt.datetime.strptime(norm_dates[0], "%Y%m%d").weekday()
    for i in range(leading_pad):
        builder.button(text="·", callback_data=NOOP)
    for date_value in norm_dates:
        label = date_value[6:8] if len(date_value) == 8 and date_value.isdigit() else date_value
        if date_value in closed_dates:
            label = f"🚫{label}"
            cb = NOOP
        elif date_value in fully_busy_dates:
            label = f"🔒{label}"
            cb = NOOP
        else:
            cb = build_admin_move_date(ap_id, date_value)
        builder.button(text=label, callback_data=cb)

    builder.button(text="« К записи", callback_data=build_admin_open(ap_id))

    grid_cells = len(norm_dates) + leading_pad
    rows = grid_cells // 7
    rem = grid_cells % 7
    adjust_pattern: list[int] = []
    adjust_pattern.extend([1, 7])
    if rows > 0:
        adjust_pattern.extend([7] * rows)
    if rem:
        adjust_pattern.append(rem)
    adjust_pattern.append(1)
    builder.adjust(*adjust_pattern)
    return builder.as_markup()


def time_keyboard(
    draft_id: str,
    time_slots: list[str],
    occupied_slots: set[str] | None = None,
    disabled_slots: set[str] | None = None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    occupied_slots = occupied_slots or set()
    disabled_slots = disabled_slots or set()

    for time_value in time_slots:
        label = time_value
        if len(time_value) == 4 and time_value.isdigit():
            label = f"{time_value[:2]}:{time_value[2:]}"
        payload = time_value
        if time_value in disabled_slots:
            label = f"⛔ {label}"
            payload = f"x_{time_value}"
        elif time_value in occupied_slots:
            label = f"🔒 {label}"
            payload = f"x_{time_value}"
        builder.button(
            text=label,
            callback_data=build_callback("time", draft_id, payload),
        )

    builder.button(
        text="⬅️ Назад",
        callback_data=build_callback("bk", draft_id),
    )
    builder.button(
        text="❌ Отмена",
        callback_data=build_callback("cancel", draft_id),
    )

    builder.adjust(2)
    return builder.as_markup()


def confirm_keyboard(draft_id: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text="✅ Подтвердить",
        callback_data=build_callback("confirm", draft_id),
    )
    builder.button(
        text="⬅️ Назад",
        callback_data=build_callback("bk", draft_id),
    )
    builder.button(
        text="❌ Отмена",
        callback_data=build_callback("cancel", draft_id),
    )

    builder.adjust(1)
    return builder.as_markup()


def contact_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Отправить контакт", request_contact=True)]
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )