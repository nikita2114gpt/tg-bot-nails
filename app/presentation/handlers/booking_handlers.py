from __future__ import annotations

from datetime import date, timedelta

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

from app.application.booking_uc import BookingUseCases
from app.config import Settings
from app.core.errors import AppError, error_to_user_message
from app.domain.enums import DraftStep
from app.presentation.callback.booking_callbacks import parse_callback_data
from app.presentation.fsm.states import BookingStates
from app.presentation.keyboards.booking_kb import (
    contact_keyboard,
    confirm_keyboard,
    date_keyboard,
    service_keyboard,
    time_keyboard,
)

router = Router()


def _available_dates(settings: Settings, count: int = 3) -> list[str]:
    today = date.today()
    max_days = max(int(settings.max_days_ahead), 0)
    if max_days <= 0:
        return [today.strftime("%Y%m%d")]
    days = min(count, max_days + 1)
    return [(today + timedelta(days=i)).strftime("%Y%m%d") for i in range(days)]


def _available_time_slots(settings: Settings, count: int = 4, start_hour: int = 10) -> list[str]:
    step_minutes = max(int(settings.slot_duration_minutes), 5)
    base_minutes = start_hour * 60
    slots: list[str] = []
    for i in range(count):
        total = base_minutes + i * step_minutes
        hour = (total // 60) % 24
        minute = total % 60
        slots.append(f"{hour:02d}{minute:02d}")
    return slots


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
    dates = _available_dates(settings)
    await _safe_edit_text(
        callback,
        "Выберите дату:",
        reply_markup=date_keyboard(draft.draft_id, dates),
    )


async def _handle_action_date(
    callback: CallbackQuery,
    state: FSMContext,
    booking_uc: BookingUseCases,
    settings: Settings,
    user_id: int,
    draft_id: str,
    date_value: str,
) -> None:
    draft = booking_uc.draft_repo.get_by_id(draft_id)
    if draft is None or draft.user_id != user_id or draft.step == DraftStep.CANCELLED:
        if callback.message is not None:
            await callback.message.answer("Сессия устарела. Напишите /start")
        await state.clear()
        return

    if draft.step != DraftStep.CHOOSE_DATE:
        await _recover_to_choose_date_from_stale(
            callback=callback,
            state=state,
            booking_uc=booking_uc,
            settings=settings,
            user_id=user_id,
            draft_id=draft_id,
        )
        return

    booking_uc.choose_date(
        draft_id=draft_id,
        user_id=user_id,
        date_value=date_value,
    )
    await state.set_state(BookingStates.choose_time)
    times = _available_time_slots(settings)
    await _safe_edit_text(
        callback,
        "Выберите время:",
        reply_markup=time_keyboard(draft_id, times),
    )


async def _handle_action_time(
    callback: CallbackQuery,
    state: FSMContext,
    booking_uc: BookingUseCases,
    settings: Settings,
    user_id: int,
    draft_id: str,
    time_value: str,
) -> None:
    draft = booking_uc.draft_repo.get_by_id(draft_id)
    if draft is None or draft.user_id != user_id:
        if callback.message is not None:
            await callback.message.answer("Сессия устарела. Напишите /start")
        await state.clear()
        return

    if draft.step != DraftStep.CHOOSE_TIME:
        await _recover_to_choose_date_from_stale(
            callback=callback,
            state=state,
            booking_uc=booking_uc,
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
        await callback.message.answer(
            "Отправьте имя и телефон в формате:\nИмя, Телефон\n\n"
            "Или нажмите кнопку отправки контакта.",
            reply_markup=contact_keyboard(),
        )


async def _recover_to_choose_date_from_stale(
    callback: CallbackQuery,
    state: FSMContext,
    booking_uc: BookingUseCases,
    settings: Settings,
    user_id: int,
    draft_id: str,
) -> None:
    """
    Унифицированное восстановление после stale inline callback (date/time).
    Приводит draft.step + FSM к консистентному choose_date и скрывает reply keyboard контакта.
    """
    draft = booking_uc.draft_repo.get_by_id(draft_id)
    if draft is None or draft.user_id != user_id or draft.step == DraftStep.CANCELLED:
        if callback.message is not None:
            await callback.message.answer("Сессия устарела. Напишите /start")
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
        await state.set_state(BookingStates.choose_service)
        if callback.message is not None:
            await callback.message.answer(
                "Сессия изменилась, выберите услугу заново.",
                reply_markup=ReplyKeyboardRemove(),
            )
        await _safe_edit_text(
            callback,
            "Выберите услугу:",
            reply_markup=service_keyboard(draft_id, booking_uc.allowed_services),
        )
        return

    await state.set_state(BookingStates.choose_date)
    if callback.message is not None:
        await callback.message.answer(
            "Сессия изменилась, выберите дату заново.",
            reply_markup=ReplyKeyboardRemove(),
        )
    dates = _available_dates(settings)
    await _safe_edit_text(
        callback,
        "Выберите дату:",
        reply_markup=date_keyboard(draft_id, dates),
    )


async def _handle_action_confirm(
    callback: CallbackQuery,
    state: FSMContext,
    booking_uc: BookingUseCases,
    user_id: int,
    draft_id: str,
) -> None:
    booking_uc.confirm_booking(
        draft_id=draft_id,
        user_id=user_id,
    )
    await state.set_state(BookingStates.confirm_done)
    await _safe_edit_text(callback, "✅ Запись подтверждена!", reply_markup=None)


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
    await state.clear()
    await _safe_edit_text(callback, "Запись отменена. Напишите /start", reply_markup=None)


async def _handle_action_back(
    callback: CallbackQuery,
    state: FSMContext,
    booking_uc: BookingUseCases,
    settings: Settings,
    user_id: int,
    draft_id: str,
) -> None:
    back_result = booking_uc.back_booking(draft_id=draft_id, user_id=user_id)

    if back_result.kind == "stale":
        if callback.message is not None:
            await callback.message.answer("Сессия устарела. Напишите /start")
        await state.clear()
        return

    if back_result.kind == "not_available":
        await _safe_edit_text(callback, "Назад здесь недоступно.", reply_markup=None)
        return

    if back_result.kind == "choose_service":
        await state.set_state(BookingStates.choose_service)
        await _safe_edit_text(
            callback,
            "Выберите услугу:",
            reply_markup=service_keyboard(draft_id, booking_uc.allowed_services),
        )
        return

    if back_result.kind == "choose_date":
        await state.set_state(BookingStates.choose_date)
        dates = _available_dates(settings)
        await _safe_edit_text(
            callback,
            "Выберите дату:",
            reply_markup=date_keyboard(draft_id, dates),
        )
        return

    if back_result.kind == "enter_contact":
        await state.set_state(BookingStates.enter_contact)
        # ReplyKeyboardMarkup -> только answer/send, не edit_text.
        if callback.message is not None:
            await callback.message.answer(
                "Отправьте имя и телефон заново:",
                reply_markup=contact_keyboard(),
            )
        return

    await _safe_edit_text(callback, "Назад здесь недоступно.", reply_markup=None)


@router.message(F.text == "/start")
async def start_handler(
    message: Message,
    state: FSMContext,
    booking_uc: BookingUseCases,
    settings: Settings,
):
    if message.from_user is None:
        await message.answer("Некорректный запрос.")
        return

    draft = booking_uc.start_booking(user_id=message.from_user.id)

    await state.set_state(BookingStates.choose_service)

    await message.answer(
        "Выберите услугу:",
        reply_markup=service_keyboard(draft.draft_id, booking_uc.allowed_services),
    )


@router.callback_query()
async def callback_router(
    callback: CallbackQuery,
    state: FSMContext,
    booking_uc: BookingUseCases,
    settings: Settings,
) -> None:
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
                settings=settings,
                user_id=user_id,
                draft_id=draft_id,
            )
        else:
            await _safe_edit_text(callback, "Неизвестное действие.", reply_markup=None)

    except ValueError:
        await _safe_edit_text(callback, "Некорректная команда. Напишите /start", reply_markup=None)
    except AppError as e:
        await _safe_edit_text(callback, error_to_user_message(e), reply_markup=None)
    except Exception:
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

        await message.answer(
            "Проверьте данные:\n\n"
            f"Услуга: {draft.service_id}\n"
            f"Дата/время: {draft.start_datetime_utc}\n"
            f"Имя: {draft.customer_name}\n"
            f"Телефон: {draft.phone_e164}",
            reply_markup=confirm_keyboard(draft.draft_id),
        )

    except AppError as e:
        await message.answer(error_to_user_message(e))
    except Exception:
        await message.answer("Произошла ошибка. Попробуйте ещё раз.")