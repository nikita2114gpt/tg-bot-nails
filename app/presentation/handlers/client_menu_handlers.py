from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.application.admin_ops_uc import AdminOpsUseCases
from app.application.appointment_uc import AppointmentUseCases, format_slot_utc_for_user
from app.application.booking_uc import ACTIVE_BOOKING_CONFLICT_MESSAGE, BookingUseCases
from app.config import Settings
from app.core.errors import AppError, ConflictError, error_to_user_message
from app.domain.enums import AppointmentStatus
from app.presentation.callback.nav_callbacks import build_client_cancel_my, parse_client
from app.presentation.fsm.states import BookingStates
from app.presentation.keyboards.booking_kb import service_keyboard
from app.presentation.keyboards.main_menu_kb import (
    BTN_ADDRESS,
    BTN_BOOK,
    BTN_MAIN_MENU,
    BTN_MY_APPT,
    BTN_SERVICES_INFO,
    main_menu_reply_keyboard,
)

router = Router(name="client_menu")


def _fmt_duration_minutes(value: int) -> str:
    h = value // 60
    m = value % 60
    if h > 0 and m > 0:
        return f"{h} ч {m} мин"
    if h > 0:
        return f"{h} ч"
    return f"{m} мин"


def _service_card_lines(
    settings: Settings,
    booking_uc: BookingUseCases,
    admin_ops_uc: AdminOpsUseCases,
) -> str:
    try:
        active_items = admin_ops_uc.list_active_services()
    except Exception:
        active_items = []

    if active_items:
        lines = ["<b>Услуги и цены</b>\n"]
        for item in active_items:
            lines.append(
                f"• {item.name}\n"
                f"  Цена: {item.price_text}\n"
                f"  Длительность: {_fmt_duration_minutes(item.duration_minutes)}\n"
            )
        return "\n".join(lines).strip()

    lines = ["<b>Услуги и цены</b>\n"]
    for svc in booking_uc.list_available_services():
        price = settings.service_price_text.get(svc, "—")
        dur = settings.service_duration_text.get(svc, "—")
        lines.append(f"• {svc}\n  Цена: {price}\n  Длительность: {dur}\n")
    return "\n".join(lines).strip()


def _contacts_text(settings: Settings, admin_ops_uc: AdminOpsUseCases) -> str:
    info = admin_ops_uc.get_salon_info(settings.salon_address, settings.salon_contacts)
    lines = ["<b>Адрес и контакты</b>\n"]
    if info.show_address:
        lines.append(f"📍 Адрес:\n{info.address_text or settings.salon_address}\n")
    if info.show_contacts:
        lines.append(f"📞 Контакты:\n{info.contacts_text or settings.salon_contacts}")
    if not info.show_address and not info.show_contacts:
        lines.append("Информация временно скрыта администратором.")
    return "\n".join(lines).strip()


def _my_appt_text(settings: Settings, ap, admin_ops_uc: AdminOpsUseCases) -> str:
    price = settings.service_price_text.get(ap.service_id, "—")
    dur = settings.service_duration_text.get(ap.service_id, "—")
    try:
        for item in admin_ops_uc.list_services():
            if item.name == ap.service_id:
                price = item.price_text or price
                dur = _fmt_duration_minutes(item.duration_minutes)
                break
    except Exception:
        pass
    when = format_slot_utc_for_user(ap.start_datetime_utc)
    return (
        "<b>Ваша запись</b>\n\n"
        f"📅 Дата и время: {when}\n"
        f"💇 Услуга: {ap.service_id}\n"
        f"💰 Цена: {price}\n"
        f"⏱ Длительность: {dur}"
    )


@router.message(Command("start"))
async def cmd_start(
    message: Message,
    state: FSMContext,
) -> None:
    await state.clear()
    await message.answer(
        "Главное меню. Выберите действие:",
        reply_markup=main_menu_reply_keyboard(),
    )


@router.message(F.text == BTN_MAIN_MENU)
async def go_main_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Главное меню.",
        reply_markup=main_menu_reply_keyboard(),
    )


@router.message(F.text == BTN_BOOK)
async def start_booking_from_menu(
    message: Message,
    state: FSMContext,
    booking_uc: BookingUseCases,
) -> None:
    if message.from_user is None:
        await message.answer("Некорректный запрос.")
        return

    await state.clear()
    try:
        draft = booking_uc.start_booking(user_id=message.from_user.id)
    except ConflictError:
        await message.answer(
            f"{ACTIVE_BOOKING_CONFLICT_MESSAGE}\n\n"
            "Нажмите «Моя запись», чтобы увидеть текущую запись.",
            reply_markup=main_menu_reply_keyboard(),
        )
        return
    await state.set_state(BookingStates.choose_service)
    await message.answer(
        "Выберите услугу:",
        reply_markup=service_keyboard(draft.draft_id, booking_uc.list_available_services()),
    )


@router.message(F.text == BTN_MY_APPT)
async def my_appointment(
    message: Message,
    state: FSMContext,
    appointment_uc: AppointmentUseCases,
    admin_ops_uc: AdminOpsUseCases,
    settings: Settings,
) -> None:
    if message.from_user is None:
        return
    ap = appointment_uc.get_my_active_appointment(message.from_user.id)
    if ap is None or ap.status != AppointmentStatus.CONFIRMED:
        builder = InlineKeyboardBuilder()
        builder.button(text="📅 Записаться", callback_data="c1|book")
        builder.button(text="🏠 В меню", callback_data="c1|menu")
        builder.adjust(1)
        await message.answer(
            "У вас нет активной записи.\n"
            "Вы можете записаться на услугу или вернуться в меню.",
            reply_markup=builder.as_markup(),
        )
        return

    builder = InlineKeyboardBuilder()
    builder.button(
        text="❌ Отменить запись",
        callback_data=build_client_cancel_my(ap.appointment_id),
    )
    builder.button(text="🏠 В меню", callback_data="c1|menu")
    builder.adjust(1)
    await message.answer(
        _my_appt_text(settings, ap, admin_ops_uc),
        reply_markup=builder.as_markup(),
    )


@router.message(F.text == BTN_SERVICES_INFO)
async def services_info(
    message: Message,
    settings: Settings,
    booking_uc: BookingUseCases,
    admin_ops_uc: AdminOpsUseCases,
) -> None:
    await message.answer(_service_card_lines(settings, booking_uc, admin_ops_uc))


@router.message(F.text == BTN_ADDRESS)
async def address_info(message: Message, settings: Settings, admin_ops_uc: AdminOpsUseCases) -> None:
    await message.answer(_contacts_text(settings, admin_ops_uc))


async def _safe_cq_answer(callback: CallbackQuery) -> None:
    try:
        await callback.answer(cache_time=0)
    except Exception:
        pass


@router.callback_query(F.data.startswith("c1"))
async def client_inline_nav(
    callback: CallbackQuery,
    state: FSMContext,
    booking_uc: BookingUseCases,
    settings: Settings,
    appointment_uc: AppointmentUseCases,
) -> None:
    await _safe_cq_answer(callback)
    if callback.data is None or callback.message is None:
        return
    try:
        action, parts = parse_client(callback.data)
    except ValueError:
        await callback.message.answer("Действие устарело. Откройте раздел заново.")
        return

    if action == "menu":
        await state.clear()
        await callback.message.answer(
            "Главное меню.",
            reply_markup=main_menu_reply_keyboard(),
        )
        return

    if action == "book":
        await state.clear()
        if callback.from_user is None:
            return
        try:
            draft = booking_uc.start_booking(user_id=callback.from_user.id)
        except ConflictError:
            await callback.message.answer(
                f"{ACTIVE_BOOKING_CONFLICT_MESSAGE}\n\n"
                "Нажмите «Моя запись», чтобы увидеть текущую запись.",
                reply_markup=main_menu_reply_keyboard(),
            )
            return
        await state.set_state(BookingStates.choose_service)
        await callback.message.answer(
            "Выберите услугу:",
            reply_markup=service_keyboard(draft.draft_id, booking_uc.list_available_services()),
        )
        return

    if action == "x" and parts:
        ap_id = parts[0]
        if callback.from_user is None:
            return
        uid = callback.from_user.id
        try:
            _, already = appointment_uc.cancel_my_appointment_by_id(uid, ap_id)
        except AppError as e:
            await callback.message.answer(error_to_user_message(e))
            return
        await state.clear()
        msg = (
            "Запись уже была отменена ранее."
            if already
            else "Запись отменена. При необходимости вы можете записаться снова."
        )
        await callback.message.answer(msg, reply_markup=main_menu_reply_keyboard())
        return

    await callback.message.answer("Действие устарело. Откройте «Моя запись» снова.")
