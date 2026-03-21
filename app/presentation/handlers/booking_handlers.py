from __future__ import annotations

from datetime import date, timedelta
from html import escape

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.application.admin_ops_uc import AdminOpsUseCases
from app.application.booking_uc import (
    BOOKING_SLOT_CONFLICT_MESSAGE,
    BookingUseCases,
)
from app.config import Settings
from app.core.errors import AppError, ConflictError, error_to_user_message
from app.domain.enums import AppointmentStatus, DraftStep
from app.presentation.callback.booking_callbacks import build_callback, parse_callback_data
from app.presentation.fsm.admin_guard import is_active_admin_fsm
from app.presentation.fsm.states import BookingStates
from app.presentation.keyboards.booking_kb import (
    contact_keyboard,
    confirm_keyboard,
    date_keyboard,
    service_keyboard,
    time_keyboard,
)

router = Router()
_CONTACT_PROMPT_MSG_ID_KEY = "contact_prompt_message_id"
_CLIENT_CALENDAR_LEGEND = "🚫 — выходной\n🔒 — занято"
_TIME_SLOT_LEGEND = "🔒 — занято"


def _calendar_holiday_and_fully_busy_sets(
    booking_uc: BookingUseCases,
    admin_ops_uc: AdminOpsUseCases,
    settings: Settings,
    dates: list[str],
    service_id: str | None,
) -> tuple[set[str], set[str]]:
    holiday = {d for d in dates if admin_ops_uc.is_day_closed(d)}
    busy = {
        d
        for d in dates
        if not admin_ops_uc.is_day_closed(d)
        and _is_day_fully_busy(booking_uc, admin_ops_uc, settings, d, service_id)
    }
    return holiday, busy


async def _force_remove_contact_keyboard(
    message_or_callback: Message | CallbackQuery | None,
) -> None:
    """Убирает reply keyboard (в т.ч. «Отправить контакт»); безопасно при отсутствии клавиатуры."""
    msg: Message | None = None
    if isinstance(message_or_callback, CallbackQuery):
        msg = message_or_callback.message
    elif isinstance(message_or_callback, Message):
        msg = message_or_callback
    if msg is None:
        return
    try:
        # zws может быть отвергнут Telegram как пустой текст.
        # Отправляем техническое сообщение с remove и сразу удаляем его.
        sent = await msg.bot.send_message(
            chat_id=msg.chat.id,
            text=".",
            reply_markup=ReplyKeyboardRemove(),
        )
        try:
            await sent.delete()
        except Exception:
            pass
    except Exception:
        pass


async def _clear_stale_contact_prompt_message(
    state: FSMContext,
    message_or_callback: Message | CallbackQuery | None,
) -> None:
    """Удаляет последнее техническое сообщение contact-prompt, если оно запомнено в FSM."""
    msg: Message | None = None
    if isinstance(message_or_callback, CallbackQuery):
        msg = message_or_callback.message
    elif isinstance(message_or_callback, Message):
        msg = message_or_callback
    if msg is None:
        return
    data = await state.get_data()
    prompt_id = data.get(_CONTACT_PROMPT_MSG_ID_KEY)
    if not isinstance(prompt_id, int):
        return
    try:
        await msg.bot.delete_message(chat_id=msg.chat.id, message_id=prompt_id)
    except Exception:
        pass
    await state.update_data(**{_CONTACT_PROMPT_MSG_ID_KEY: None})


async def _send_contact_prompt(
    state: FSMContext,
    message: Message,
    text: str,
) -> None:
    """Показывает prompt контакта и запоминает message_id для последующей очистки."""
    sent = await message.answer(text, reply_markup=contact_keyboard())
    await state.update_data(**{_CONTACT_PROMPT_MSG_ID_KEY: sent.message_id})


def _available_dates(settings: Settings, admin_ops_uc: AdminOpsUseCases, count: int = 3) -> list[str]:
    today = date.today()
    days = max(count, 30)
    raw = [(today + timedelta(days=i)).strftime("%Y%m%d") for i in range(days)]
    return raw[: max(count, 30)]


def _available_time_slots(
    settings: Settings,
    admin_ops_uc: AdminOpsUseCases,
    ymd: str,
    service_id: str | None = None,
) -> list[str]:
    # Safe fallback default model for runtime slot generation.
    # If schedule settings are unavailable/invalid, use 08:00-20:00 with 60m step.
    start_minutes = 8 * 60
    end_minutes = 20 * 60
    step_minutes = 60
    try:
        if admin_ops_uc.is_day_closed(ymd):
            return []
        sched = admin_ops_uc.get_effective_schedule_for_date(ymd)
        open_raw = (sched.open_time_hhmm or "").strip()
        close_raw = (sched.close_time_hhmm or "").strip()
        if len(open_raw) == 4 and open_raw.isdigit():
            start_minutes = int(open_raw[:2]) * 60 + int(open_raw[2:])
        if len(close_raw) == 4 and close_raw.isdigit():
            end_minutes = int(close_raw[:2]) * 60 + int(close_raw[2:])
        if service_id:
            step_minutes = max(admin_ops_uc.get_service_slot_step_minutes(service_id), 5)
        elif isinstance(sched.slot_minutes, int) and sched.slot_minutes > 0:
            step_minutes = max(sched.slot_minutes, 5)
        if end_minutes < start_minutes:
            end_minutes = start_minutes
    except Exception:
        pass

    slots: list[str] = []
    total = start_minutes
    max_slots = 200
    while total <= end_minutes and len(slots) < max_slots:
        hour = (total // 60) % 24
        minute = total % 60
        slots.append(f"{hour:02d}{minute:02d}")
        total += step_minutes
    if not slots:
        slots = ["0800", "0900", "1000", "1100", "1200", "1300", "1400", "1500", "1600", "1700", "1800", "1900", "2000"]
    return slots


def _hhmm_to_minutes(hhmm: str) -> int | None:
    if len(hhmm) != 4 or not hhmm.isdigit():
        return None
    return int(hhmm[:2]) * 60 + int(hhmm[2:])


def _occupied_slots_with_duration(
    booking_uc: BookingUseCases,
    admin_ops_uc: AdminOpsUseCases,
    ymd: str,
    candidate_slots: list[str],
) -> set[str]:
    try:
        rows = booking_uc.appointment_repo.list_starting_with_date(ymd)
    except Exception:
        rows = []
    occupied: set[str] = set()
    candidate_minutes = {
        slot: minute for slot in candidate_slots if (minute := _hhmm_to_minutes(slot)) is not None
    }
    for ap in rows:
        if ap.status.value == "cancelled":
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
        for slot, slot_min in candidate_minutes.items():
            if start_min <= slot_min < end_min:
                occupied.add(slot)
    return occupied


def _is_day_fully_busy(
    booking_uc: BookingUseCases,
    admin_ops_uc: AdminOpsUseCases,
    settings: Settings,
    ymd: str,
    service_id: str | None,
) -> bool:
    if admin_ops_uc.is_day_closed(ymd):
        return True
    slots = _available_time_slots(settings, admin_ops_uc, ymd, service_id)
    if not slots:
        return True
    occupied = _occupied_slots_with_duration(booking_uc, admin_ops_uc, ymd, slots)
    return all(slot in occupied for slot in slots)


async def _safe_callback_answer(callback: CallbackQuery) -> None:
    try:
        await callback.answer(cache_time=0)
    except Exception:
        pass


async def _safe_edit_text(callback: CallbackQuery, text: str, reply_markup=None) -> None:
    if callback.message is None:
        return
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except Exception:
        # edit_text может не сработать (контент уже такой же и т.п.)
        await callback.message.answer(text, reply_markup=reply_markup)


async def _handle_action_svc(
    callback: CallbackQuery,
    state: FSMContext,
    booking_uc: BookingUseCases,
    admin_ops_uc: AdminOpsUseCases,
    settings: Settings,
    user_id: int,
    draft_id: str,
    service_id: str,
) -> None:
    draft = booking_uc.choose_service(
        draft_id=draft_id,
        user_id=user_id,
        service_id=service_id,
    )
    await state.set_state(BookingStates.choose_date)
    dates = _available_dates(settings, admin_ops_uc)
    holiday_dates, busy_dates = _calendar_holiday_and_fully_busy_sets(
        booking_uc, admin_ops_uc, settings, dates, service_id
    )
    if not dates:
        await _safe_edit_text(callback, "Нет доступных дат для записи. Обратитесь к администратору.")
        return
    await _safe_edit_text(
        callback,
        f"Когда вам удобно? 📅\n{_CLIENT_CALENDAR_LEGEND}",
        reply_markup=date_keyboard(
            draft.draft_id,
            dates,
            closed_dates=holiday_dates,
            fully_busy_dates=busy_dates,
        ),
    )


async def _handle_action_date(
    callback: CallbackQuery,
    state: FSMContext,
    booking_uc: BookingUseCases,
    admin_ops_uc: AdminOpsUseCases,
    settings: Settings,
    user_id: int,
    draft_id: str,
    date_value: str,
) -> None:
    draft = booking_uc.draft_repo.get_by_id(draft_id)
    if draft is None or draft.user_id != user_id or draft.step == DraftStep.CANCELLED:
        if callback.message is not None:
            await callback.message.answer("Сессия устарела. Напишите /start")
        await _force_remove_contact_keyboard(callback.message)
        await state.clear()
        return

    if draft.step != DraftStep.CHOOSE_DATE:
        skip_notice = False
        if (date_value or "").startswith("x_"):
            pd = (date_value or "")[2:]
            if (
                len(pd) == 8
                and pd.isdigit()
                and not admin_ops_uc.is_day_closed(pd)
                and _is_day_fully_busy(
                    booking_uc,
                    admin_ops_uc,
                    settings,
                    pd,
                    draft.service_id,
                )
            ):
                skip_notice = True
        await _recover_to_choose_date_from_stale(
            callback=callback,
            state=state,
            booking_uc=booking_uc,
            admin_ops_uc=admin_ops_uc,
            settings=settings,
            user_id=user_id,
            draft_id=draft_id,
            skip_session_changed_notice=skip_notice,
        )
        return

    picked_date = date_value
    if (date_value or "").startswith("x_"):
        picked_date = (date_value or "")[2:]
        # Закрытые/технические ячейки календаря не двигают сценарий и не перерисовывают экран.
        if len(picked_date) != 8 or not picked_date.isdigit():
            return
        if admin_ops_uc.is_day_closed(picked_date):
            return
        if _is_day_fully_busy(
            booking_uc,
            admin_ops_uc,
            settings,
            picked_date,
            draft.service_id,
        ):
            return
    if admin_ops_uc.is_day_closed(picked_date):
        dates = _available_dates(settings, admin_ops_uc)
        holiday_dates, busy_dates = _calendar_holiday_and_fully_busy_sets(
            booking_uc,
            admin_ops_uc,
            settings,
            dates,
            (draft.service_id if draft is not None else None),
        )
        await _safe_edit_text(
            callback,
            "Этот день закрыт для записи. Выберите рабочий день.",
            reply_markup=date_keyboard(
                draft_id,
                dates,
                closed_dates=holiday_dates,
                fully_busy_dates=busy_dates,
            ),
        )
        await state.set_state(BookingStates.choose_date)
        return

    booking_uc.choose_date(
        draft_id=draft_id,
        user_id=user_id,
        date_value=picked_date,
    )
    await state.set_state(BookingStates.choose_time)
    times = _available_time_slots(settings, admin_ops_uc, picked_date, draft.service_id)
    occupied_slots = _occupied_slots_with_duration(booking_uc, admin_ops_uc, picked_date, times)
    free_exists = any(slot not in occupied_slots for slot in times)
    if not free_exists:
        await state.set_state(BookingStates.choose_date)
        dates_fb = _available_dates(settings, admin_ops_uc)
        h_fb, b_fb = _calendar_holiday_and_fully_busy_sets(
            booking_uc, admin_ops_uc, settings, dates_fb, draft.service_id
        )
        b_fb = b_fb | {picked_date}
        await _safe_edit_text(
            callback,
            f"На этот день свободных слотов нет. Выберите другую дату:\n{_CLIENT_CALENDAR_LEGEND}",
            reply_markup=date_keyboard(
                draft_id,
                dates_fb,
                closed_dates=h_fb,
                fully_busy_dates=b_fb,
            ),
        )
        return
    if not times:
        await state.set_state(BookingStates.choose_date)
        dates_nt = _available_dates(settings, admin_ops_uc)
        h_nt, b_nt = _calendar_holiday_and_fully_busy_sets(
            booking_uc, admin_ops_uc, settings, dates_nt, draft.service_id
        )
        await _safe_edit_text(
            callback,
            f"Этот день закрыт для записи. Выберите другую дату:\n{_CLIENT_CALENDAR_LEGEND}",
            reply_markup=date_keyboard(
                draft_id,
                dates_nt,
                closed_dates=h_nt,
                fully_busy_dates=b_nt,
            ),
        )
        return
    await _safe_edit_text(
        callback,
        f"Выберите время ⏱\n{_TIME_SLOT_LEGEND}",
        reply_markup=time_keyboard(draft_id, times, occupied_slots=occupied_slots),
    )


async def _handle_action_time(
    callback: CallbackQuery,
    state: FSMContext,
    booking_uc: BookingUseCases,
    admin_ops_uc: AdminOpsUseCases,
    settings: Settings,
    user_id: int,
    draft_id: str,
    time_value: str,
) -> None:
    draft = booking_uc.draft_repo.get_by_id(draft_id)
    if draft is None or draft.user_id != user_id:
        if callback.message is not None:
            await callback.message.answer("Сессия устарела. Напишите /start")
        await _force_remove_contact_keyboard(callback.message)
        await state.clear()
        return

    # Занятый слот: до stale-recovery, иначе старый inline «время» уводит в recover вместо no-op.
    if (time_value or "").startswith("x_"):
        return

    if draft.step != DraftStep.CHOOSE_TIME:
        await _recover_to_choose_date_from_stale(
            callback=callback,
            state=state,
            booking_uc=booking_uc,
            admin_ops_uc=admin_ops_uc,
            settings=settings,
            user_id=user_id,
            draft_id=draft_id,
        )
        return

    booking_uc.choose_time(
        draft_id=draft_id,
        user_id=user_id,
        time_value=time_value,
    )
    await state.set_state(BookingStates.enter_contact)
    # Контакт просим через ReplyKeyboardMarkup; edit_text с таким reply_markup может упасть.
    if callback.message is not None:
        await _clear_stale_contact_prompt_message(state, callback)
        await _force_remove_contact_keyboard(callback)
        await _send_contact_prompt(
            state=state,
            message=callback.message,
            text=(
                "Отправьте имя и телефон в формате:\nИмя, Телефон\n\n"
                "Или нажмите кнопку отправки контакта."
            ),
        )


async def _recover_to_choose_date_from_stale(
    callback: CallbackQuery,
    state: FSMContext,
    booking_uc: BookingUseCases,
    admin_ops_uc: AdminOpsUseCases,
    settings: Settings,
    user_id: int,
    draft_id: str,
    *,
    skip_session_changed_notice: bool = False,
) -> None:
    """
    Унифицированное восстановление после stale inline callback (date/time).
    Приводит draft.step + FSM к консистентному choose_date и скрывает reply keyboard контакта.
    """
    draft = booking_uc.draft_repo.get_by_id(draft_id)
    if draft is None or draft.user_id != user_id or draft.step == DraftStep.CANCELLED:
        if callback.message is not None:
            await callback.message.answer("Сессия устарела. Напишите /start")
        await _clear_stale_contact_prompt_message(state, callback)
        await _force_remove_contact_keyboard(callback.message)
        await state.clear()
        return

    # Если draft ушёл дальше (choose_time/enter_contact/confirm), откатываем через use-case.
    if draft.step in {DraftStep.CHOOSE_TIME, DraftStep.ENTER_CONTACT}:
        booking_uc.back_booking(draft_id=draft_id, user_id=user_id)
    elif draft.step == DraftStep.CONFIRM:
        # confirm -> enter_contact -> choose_date
        booking_uc.back_booking(draft_id=draft_id, user_id=user_id)
        booking_uc.back_booking(draft_id=draft_id, user_id=user_id)
    elif draft.step == DraftStep.CHOOSE_SERVICE:
        # stale callbacks from deeper steps against fresh draft:
        # безопаснее показать актуальный первый шаг без "технической" ошибки.
        await _clear_stale_contact_prompt_message(state, callback)
        await state.set_state(BookingStates.choose_service)
        if callback.message is not None:
            await callback.message.answer(
                "Сессия изменилась, выберите услугу заново.",
                reply_markup=ReplyKeyboardRemove(),
            )
        await _safe_edit_text(
            callback,
            "Выберите услугу:",
            reply_markup=service_keyboard(draft_id, booking_uc.list_available_services()),
        )
        return

    draft = booking_uc.draft_repo.get_by_id(draft_id)
    await _clear_stale_contact_prompt_message(state, callback)
    await state.set_state(BookingStates.choose_date)
    if callback.message is not None and not skip_session_changed_notice:
        await callback.message.answer(
            "Сессия изменилась, выберите дату заново.",
            reply_markup=ReplyKeyboardRemove(),
        )
    dates = _available_dates(settings, admin_ops_uc)
    holiday_dates, busy_dates = _calendar_holiday_and_fully_busy_sets(
        booking_uc,
        admin_ops_uc,
        settings,
        dates,
        (draft.service_id if draft is not None else None),
    )
    if not dates:
        await _safe_edit_text(callback, "Нет доступных дат для записи. Обратитесь к администратору.")
        return
    await _safe_edit_text(
        callback,
        f"Когда вам удобно? 📅\n{_CLIENT_CALENDAR_LEGEND}",
        reply_markup=date_keyboard(
            draft_id,
            dates,
            closed_dates=holiday_dates,
            fully_busy_dates=busy_dates,
        ),
    )


async def _confirm_failure_cleanup(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await _clear_stale_contact_prompt_message(state, callback)
    await _force_remove_contact_keyboard(callback)
    await state.clear()


async def _apply_confirm_success_ui(
    callback: CallbackQuery,
    state: FSMContext,
    ap,
) -> None:
    from app.presentation.keyboards.main_menu_kb import post_confirm_reply_keyboard

    await _clear_stale_contact_prompt_message(state, callback)
    await state.set_state(BookingStates.confirm_done)
    await _force_remove_contact_keyboard(callback)
    success_body = _format_success_from_appointment(ap)
    try:
        await _safe_edit_text(callback, success_body, reply_markup=None)
    except Exception:
        if callback.message is not None:
            try:
                await callback.message.answer(success_body)
            except Exception:
                pass
    if callback.message is not None:
        try:
            await callback.message.answer(
                "\u200b",
                reply_markup=post_confirm_reply_keyboard(),
            )
        except Exception:
            pass


def _format_success_from_appointment(ap) -> str:
    """Формирует HTML-безопасный текст успешной записи из Appointment."""
    date_text = "—"
    time_text = "—"
    s = ap.start_datetime_utc or ""
    if len(s) >= 13 and "T" in s:
        d, t = s.split("T", 1)
        if len(d) == 8 and d.isdigit():
            date_text = f"{d[6:8]}.{d[4:6]}.{d[0:4]}"
        if len(t) >= 4 and t[:4].isdigit():
            time_text = f"{t[:2]}:{t[2:4]}"
    return (
        "Вы записаны ✨\n\n"
        "Ждём вас:\n"
        f"📅 {date_text}\n"
        f"🕒 {time_text}\n"
        f"💇 {escape(str(ap.service_id or '—'))}"
    )


async def _handle_action_confirm(
    callback: CallbackQuery,
    state: FSMContext,
    booking_uc: BookingUseCases,
    user_id: int,
    draft_id: str,
) -> None:
    draft = booking_uc.draft_repo.get_by_id(draft_id)
    if draft is None or draft.user_id != user_id:
        if callback.message is not None:
            await callback.message.answer("Сессия устарела. Напишите /start")
        await _force_remove_contact_keyboard(callback.message)
        await state.clear()
        return

    def _confirmed_for_draft() -> object | None:
        ap = booking_uc.appointment_repo.get_by_draft_id(draft_id)
        if (
            ap is not None
            and ap.status == AppointmentStatus.CONFIRMED
            and int(ap.user_id) == int(user_id)
        ):
            return ap
        return None

    try:
        ap_ready = _confirmed_for_draft()
        if ap_ready is not None:
            try:
                await _apply_confirm_success_ui(callback, state, ap_ready)
            except Exception:
                ap2 = _confirmed_for_draft()
                if ap2 is not None:
                    await _apply_confirm_success_ui(callback, state, ap2)
                else:
                    raise
            return

        try:
            appointment = booking_uc.confirm_booking(draft_id=draft_id, user_id=user_id)
        except ConflictError:
            ap_recover = _confirmed_for_draft()
            if ap_recover is not None:
                try:
                    await _apply_confirm_success_ui(callback, state, ap_recover)
                except Exception:
                    ap2 = _confirmed_for_draft()
                    if ap2 is not None:
                        await _apply_confirm_success_ui(callback, state, ap2)
                    else:
                        raise
                return
            await _confirm_failure_cleanup(callback, state)
            raise
        except AppError:
            ap_recover = _confirmed_for_draft()
            if ap_recover is not None:
                try:
                    await _apply_confirm_success_ui(callback, state, ap_recover)
                except Exception:
                    ap2 = _confirmed_for_draft()
                    if ap2 is not None:
                        await _apply_confirm_success_ui(callback, state, ap2)
                    else:
                        raise
                return
            await _confirm_failure_cleanup(callback, state)
            raise

        try:
            await _apply_confirm_success_ui(callback, state, appointment)
        except Exception:
            ap_recover = _confirmed_for_draft()
            if ap_recover is not None:
                await _apply_confirm_success_ui(callback, state, ap_recover)
                return
            await _confirm_failure_cleanup(callback, state)
            raise
    except Exception:
        # Последний рубеж: не-AppError из confirm_booking или неожиданный сбой в ветках выше.
        # Восстановление «успех» только если в БД уже есть CONFIRMED по draft_id (доказанный domain success).
        # Ошибки показа успеха не проглатываем — пробрасываем в callback_router.
        ap_fallback = _confirmed_for_draft()
        if ap_fallback is not None:
            await _apply_confirm_success_ui(callback, state, ap_fallback)
            return
        await _confirm_failure_cleanup(callback, state)
        raise


async def _handle_action_cancel(
    callback: CallbackQuery,
    state: FSMContext,
    booking_uc: BookingUseCases,
    user_id: int,
    draft_id: str,
) -> None:
    booking_uc.cancel_booking(
        draft_id=draft_id,
        user_id=user_id,
    )
    await _clear_stale_contact_prompt_message(state, callback)
    await state.clear()
    if callback.message is not None:
        await callback.message.answer("Запись отменена.", reply_markup=ReplyKeyboardRemove())
        from app.presentation.keyboards.main_menu_kb import main_menu_reply_keyboard
        await callback.message.answer("Вы вернулись в меню.", reply_markup=main_menu_reply_keyboard())
    await _safe_edit_text(callback, "Запись отменена.", reply_markup=None)


async def _handle_action_back(
    callback: CallbackQuery,
    state: FSMContext,
    booking_uc: BookingUseCases,
    admin_ops_uc: AdminOpsUseCases,
    settings: Settings,
    user_id: int,
    draft_id: str,
) -> None:
    back_result = booking_uc.back_booking(draft_id=draft_id, user_id=user_id)

    if back_result.kind == "stale":
        if callback.message is not None:
            await callback.message.answer("Сессия устарела. Напишите /start")
        await _clear_stale_contact_prompt_message(state, callback)
        await _force_remove_contact_keyboard(callback.message)
        await state.clear()
        return

    if back_result.kind == "not_available":
        await _safe_edit_text(callback, "Назад здесь недоступно.", reply_markup=None)
        return

    if back_result.kind == "choose_service":
        await _clear_stale_contact_prompt_message(state, callback)
        await state.set_state(BookingStates.choose_service)
        await _force_remove_contact_keyboard(callback)
        await _safe_edit_text(
            callback,
            "Выберите услугу:",
            reply_markup=service_keyboard(draft_id, booking_uc.list_available_services()),
        )
        return

    if back_result.kind == "choose_date":
        await _clear_stale_contact_prompt_message(state, callback)
        await state.set_state(BookingStates.choose_date)
        await _force_remove_contact_keyboard(callback)
        dates = _available_dates(settings, admin_ops_uc)
        draft_for_dates = booking_uc.draft_repo.get_by_id(draft_id)
        draft_service_id = draft_for_dates.service_id if draft_for_dates is not None else None
        holiday_dates, busy_dates = _calendar_holiday_and_fully_busy_sets(
            booking_uc, admin_ops_uc, settings, dates, draft_service_id
        )
        if not dates:
            await _safe_edit_text(callback, "Нет доступных дат для записи. Обратитесь к администратору.")
            return
        await _safe_edit_text(
            callback,
            f"Когда вам удобно? 📅\n{_CLIENT_CALENDAR_LEGEND}",
            reply_markup=date_keyboard(
                draft_id,
                dates,
                closed_dates=holiday_dates,
                fully_busy_dates=busy_dates,
            ),
        )
        return

    if back_result.kind == "enter_contact":
        await state.set_state(BookingStates.enter_contact)
        # ReplyKeyboardMarkup -> только answer/send, не edit_text.
        if callback.message is not None:
            await _clear_stale_contact_prompt_message(state, callback)
            await _force_remove_contact_keyboard(callback)
            await _send_contact_prompt(
                state=state,
                message=callback.message,
                text="Отправьте имя и телефон заново:",
            )
        return

    await _clear_stale_contact_prompt_message(state, callback)
    await _force_remove_contact_keyboard(callback)
    await _safe_edit_text(callback, "Назад здесь недоступно.", reply_markup=None)


@router.callback_query(F.data.startswith("b2:"))
async def callback_router(
    callback: CallbackQuery,
    state: FSMContext,
    booking_uc: BookingUseCases,
    admin_ops_uc: AdminOpsUseCases,
    settings: Settings,
) -> None:
    if await is_active_admin_fsm(state):
        try:
            await callback.answer(
                "Сначала завершите ввод в админке или откройте /admin.",
                show_alert=True,
            )
        except Exception:
            pass
        return
    await _safe_callback_answer(callback)

    if callback.data is None or callback.message is None:
        return

    if callback.from_user is None:
        await callback.message.answer("Некорректный пользователь.")
        return
    user_id = callback.from_user.id

    try:
        data = parse_callback_data(callback.data)
        action = data["action"]
        draft_id = data["draft_id"]
        payload = data["payload"]

        if action == "svc":
            await _handle_action_svc(
                callback=callback,
                state=state,
                booking_uc=booking_uc,
                admin_ops_uc=admin_ops_uc,
                settings=settings,
                user_id=user_id,
                draft_id=draft_id,
                service_id=payload,
            )
        elif action == "date":
            await _handle_action_date(
                callback=callback,
                state=state,
                booking_uc=booking_uc,
                admin_ops_uc=admin_ops_uc,
                settings=settings,
                user_id=user_id,
                draft_id=draft_id,
                date_value=payload,
            )
        elif action == "time":
            await _handle_action_time(
                callback=callback,
                state=state,
                booking_uc=booking_uc,
                admin_ops_uc=admin_ops_uc,
                settings=settings,
                user_id=user_id,
                draft_id=draft_id,
                time_value=payload,
            )
        elif action == "confirm":
            await _handle_action_confirm(
                callback=callback,
                state=state,
                booking_uc=booking_uc,
                user_id=user_id,
                draft_id=draft_id,
            )
        elif action == "cancel":
            await _handle_action_cancel(
                callback=callback,
                state=state,
                booking_uc=booking_uc,
                user_id=user_id,
                draft_id=draft_id,
            )
        elif action == "bk":
            await _handle_action_back(
                callback=callback,
                state=state,
                booking_uc=booking_uc,
                admin_ops_uc=admin_ops_uc,
                settings=settings,
                user_id=user_id,
                draft_id=draft_id,
            )
        else:
            await _safe_edit_text(callback, "Неизвестное действие.", reply_markup=None)

    except ValueError:
        await _clear_stale_contact_prompt_message(state, callback)
        await _force_remove_contact_keyboard(callback)
        await _safe_edit_text(callback, "Некорректная команда. Напишите /start", reply_markup=None)
    except AppError as e:
        await _clear_stale_contact_prompt_message(state, callback)
        await _force_remove_contact_keyboard(callback)
        text = error_to_user_message(e)
        if text == BOOKING_SLOT_CONFLICT_MESSAGE:
            b = InlineKeyboardBuilder()
            b.button(text="🕒 Выбрать другое время", callback_data=build_callback("bk", draft_id))
            b.button(text="🏠 В меню", callback_data="c1|menu")
            b.adjust(1)
            await _safe_edit_text(callback, text, reply_markup=b.as_markup())
            return
        await _safe_edit_text(callback, text, reply_markup=None)
    except Exception:
        await _clear_stale_contact_prompt_message(state, callback)
        await _force_remove_contact_keyboard(callback)
        await _safe_edit_text(callback, "Произошла ошибка. Попробуйте ещё раз.", reply_markup=None)


@router.message(BookingStates.enter_contact)
async def contact_handler(
    message: Message,
    state: FSMContext,
    booking_uc: BookingUseCases,
):
    if message.from_user is None:
        await message.answer("Некорректный пользователь.")
        return

    draft = booking_uc.draft_repo.get_by_user_id(message.from_user.id)

    if draft is None:
        await message.answer("Сессия не найдена. Напишите /start")
        await _clear_stale_contact_prompt_message(state, message)
        await _force_remove_contact_keyboard(message)
        await state.clear()
        return

    if draft.step != DraftStep.ENTER_CONTACT:
        await message.answer("Сессия устарела. Напишите /start")
        await _clear_stale_contact_prompt_message(state, message)
        await _force_remove_contact_keyboard(message)
        await state.clear()
        return

    if message.contact:
        phone = message.contact.phone_number
        name = message.from_user.full_name
    else:
        if not message.text:
            await message.answer("Введите: Имя, Телефон")
            return

        parts = message.text.split(",")

        if len(parts) != 2:
            await message.answer("Введите в формате: Имя, Телефон")
            return

        name = parts[0].strip()
        phone = parts[1].strip()

    try:
        draft = booking_uc.enter_contact(
            draft_id=draft.draft_id,
            user_id=message.from_user.id,
            name=name,
            phone=phone,
        )

        await state.set_state(BookingStates.confirm)
        time_text = "—"
        if draft.appointment_time and len(draft.appointment_time) >= 4:
            time_text = f"{draft.appointment_time[:2]}:{draft.appointment_time[2:4]}"
        date_text = "—"
        if draft.appointment_date and len(draft.appointment_date) == 8:
            date_text = (
                f"{draft.appointment_date[6:8]}."
                f"{draft.appointment_date[4:6]}."
                f"{draft.appointment_date[0:4]}"
            )

        await _clear_stale_contact_prompt_message(state, message)
        await _force_remove_contact_keyboard(message)
        await message.answer(
            "Проверьте запись ✨\n\n"
            f"💇 {escape(draft.service_id or '—')}\n"
            f"📅 {date_text}\n"
            f"🕒 {time_text}\n\n"
            f"👤 {escape(draft.customer_name or '—')}\n"
            f"📞 {escape(draft.phone_e164 or '—')}",
            reply_markup=confirm_keyboard(draft.draft_id),
        )

    except AppError as e:
        await _clear_stale_contact_prompt_message(state, message)
        await _force_remove_contact_keyboard(message)
        await message.answer(error_to_user_message(e))
    except Exception:
        await _clear_stale_contact_prompt_message(state, message)
        await _force_remove_contact_keyboard(message)
        await message.answer("Произошла ошибка. Попробуйте ещё раз.")


@router.message(F.contact, ~StateFilter(BookingStates.enter_contact))
async def contact_shared_outside_enter_contact(
    message: Message,
    state: FSMContext,
) -> None:
    """Контакт вне шага enter_contact — не вызываем enter_contact/confirm, только снимаем клавиатуру."""
    await _clear_stale_contact_prompt_message(state, message)
    await _force_remove_contact_keyboard(message)
    await message.answer("Сессия устарела. Напишите /start")
    await state.clear()