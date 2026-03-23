from __future__ import annotations

import html
import re

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.application.admin_ops_uc import AdminOpsUseCases
from app.application.appointment_uc import AppointmentUseCases, format_slot_utc_for_user
from app.application.booking_uc import BookingUseCases
from app.application.service_catalog_view import (
    format_service_block,
    resolve_service_price_and_duration,
)
from app.config import Settings
from app.core.errors import AppError, ConflictError, error_to_user_message
from app.domain.enums import AppointmentStatus
from app.presentation.callback.nav_callbacks import build_client_cancel_my, parse_client
from app.presentation.fsm.admin_guard import is_active_admin_fsm
from app.presentation.fsm.states import AdminStates, BookingStates
from app.presentation.client_perf import PerfSpan
from app.presentation.handlers.booking_handlers import (
    BOOKING_UI_MSG_ID_KEY,
    invalidate_previous_booking_ui,
    remember_booking_ui_message,
)
from app.presentation.keyboards.booking_kb import service_keyboard
from app.presentation.keyboards.main_menu_kb import (
    BTN_ADDRESS,
    BTN_BOOK,
    BTN_MAIN_MENU,
    BTN_MENU,
    BTN_MY_APPT,
    BTN_PRICE_LIST,
    main_menu_reply_keyboard,
)

router = Router(name="client_menu")


def _start_booking_conflict_user_text(exc: ConflictError) -> str:
    """
    start_booking() может кинуть ConflictError из blacklist или из проверки активной записи.
    Нельзя подменять текст исключения на «активная запись» — иначе blacklist выглядит как дубликат записи.
    """
    return str(exc)


def _fmt_duration_minutes(value: int) -> str:
    h = value // 60
    m = value % 60
    if h > 0 and m > 0:
        return f"{h} ч {m} мин"
    if h > 0:
        return f"{h} ч"
    return f"{m} мин"


def _price_list_client_text(admin_ops_uc: AdminOpsUseCases) -> str:
    def _parse_price_block(raw: str) -> tuple[str, str, str | None]:
        text = (raw or "").strip()
        if not text:
            return "—", "—", None
        title = text.splitlines()[0].strip() or text
        price: str | None = None
        duration: str | None = None

        m_price = re.search(r"(?:цена)\s*[:\-]?\s*([^\n]+)", text, flags=re.IGNORECASE)
        if m_price:
            price = m_price.group(1).strip()
        m_dur = re.search(r"(?:длительность)\s*[:\-]?\s*([^\n]+)", text, flags=re.IGNORECASE)
        if m_dur:
            duration = m_dur.group(1).strip()
        if duration is None:
            m_paren = re.search(r"\(([^)]*(?:мин|ч|час)[^)]*)\)", text, flags=re.IGNORECASE)
            if m_paren:
                duration = m_paren.group(1).strip()

        if "," in text and price is None:
            parts = [x.strip() for x in text.split(",") if x.strip()]
            if parts:
                title = parts[0]
            if len(parts) >= 2:
                price = parts[1]
            if len(parts) >= 3 and duration is None:
                duration = parts[2]

        if price is None:
            m_num = re.search(r"(\d[\d\s]*(?:[.,]\d+)?\s*(?:₽|руб\.?|р\.?)?)", text, flags=re.IGNORECASE)
            if m_num:
                price = m_num.group(1).strip()
        if not price:
            price = "—"
        if duration:
            duration = duration.strip()
        return title, price, duration or None

    try:
        items = admin_ops_uc.list_active_price_items()
    except Exception:
        items = []
    if not items:
        return "<b>Прайс ✨</b>\n\nПока нет позиций — загляните позже."
    blocks: list[str] = []
    for item in items:
        title, price, duration = _parse_price_block(item.display_text)
        block_lines = [
            f"• {html.escape(title)}",
            f"Цена: {html.escape(price)}",
        ]
        if duration:
            block_lines.append(f"Длительность: {html.escape(duration)}")
        blocks.append("\n".join(block_lines))
    return "<b>Прайс ✨</b>\n\n" + "\n\n".join(blocks)


def _contacts_text(settings: Settings, admin_ops_uc: AdminOpsUseCases) -> str:
    info = admin_ops_uc.get_salon_info(settings.salon_address, settings.salon_contacts)
    lines = ["<b>Контактная информация 📍</b>\n"]
    if info.show_address:
        lines.append(f"📍 Адрес:\n{info.address_text or settings.salon_address}\n")
    if info.show_contacts:
        lines.append(f"📞 Контакты:\n{info.contacts_text or settings.salon_contacts}")
    if not info.show_address and not info.show_contacts:
        lines.append("Информация временно скрыта администратором.")
    return "\n".join(lines).strip()


def _my_appt_text(settings: Settings, ap, admin_ops_uc: AdminOpsUseCases) -> str:
    price, dur = resolve_service_price_and_duration(
        getattr(admin_ops_uc, "service_repo", None),
        str(ap.service_id or ""),
    )
    when = format_slot_utc_for_user(ap.start_datetime_utc)
    admin_line = ""
    try:
        if int(ap.user_id) == 0:
            admin_line = "\n\n🛠 Создано админом"
    except (TypeError, ValueError):
        pass
    return (
        "<b>Ваша активная запись 📌</b>\n"
        "Ниже указаны все актуальные данные.\n\n"
        f"📅 Дата и время: {when}\n"
        f"{format_service_block(ap.service_id or '—', price, dur)}\n"
        f"👤 Клиент: {ap.customer_name or '—'}\n"
        f"📞 Телефон: {ap.phone_e164 or '—'}"
        f"{admin_line}"
    )


@router.message(Command("start"), ~StateFilter(AdminStates))
async def cmd_start(
    message: Message,
    state: FSMContext,
) -> None:
    await state.clear()
    await message.answer("Здравствуйте! 🌸\nДобро пожаловать в бот записи.")
    await message.answer(
        "Выберите, что хотите сделать.",
        reply_markup=main_menu_reply_keyboard(),
    )


@router.message(F.text == BTN_MAIN_MENU, ~StateFilter(AdminStates))
@router.message(F.text == BTN_MENU, ~StateFilter(AdminStates))
async def go_main_menu(message: Message, state: FSMContext) -> None:
    perf = PerfSpan("client_go_main_menu")
    perf.mark("handler_entry")
    await state.clear()
    perf.mark("after_fsm_clear")
    perf.mark("after_reply_keyboard_remove")
    await message.answer(
        "Вы вернулись в клиентское меню.",
        reply_markup=main_menu_reply_keyboard(),
    )
    perf.mark("done")


@router.message(F.text == BTN_BOOK, ~StateFilter(AdminStates))
async def start_booking_from_menu(
    message: Message,
    state: FSMContext,
    booking_uc: BookingUseCases,
) -> None:
    perf = PerfSpan('client_btn_book')
    perf.mark("handler_entry")
    if message.from_user is None:
        await message.answer("Некорректный запрос.")
        return

    prev_data = await state.get_data()
    perf.mark("after_fsm_get_data")
    old_ui = prev_data.get(BOOKING_UI_MSG_ID_KEY)
    await state.clear()
    perf.mark("after_fsm_clear")
    try:
        await invalidate_previous_booking_ui(message.bot, message.chat.id, old_ui)
    except Exception:
        pass
    perf.mark("after_invalidate_previous_ui")
    perf.mark("after_reply_keyboard_remove")
    try:
        draft = booking_uc.start_booking(user_id=message.from_user.id)
    except ConflictError as e:
        await message.answer(
            _start_booking_conflict_user_text(e),
            reply_markup=main_menu_reply_keyboard(),
        )
        perf.mark("done_conflict")
        return
    perf.mark("after_start_booking_uc_incl_cancel_others")
    await state.set_state(BookingStates.choose_service)
    perf.mark("after_set_state_choose_service")
    sent = await message.answer(
        "Шаг 1/5 — выберите услугу",
        reply_markup=service_keyboard(
            draft.draft_id,
            booking_uc.list_available_services(),
            getattr(booking_uc, "service_catalog_repo", None),
        ),
    )
    perf.mark("after_answer_service_keyboard")
    await remember_booking_ui_message(state, sent)
    perf.mark("done")


@router.message(F.text == BTN_MY_APPT, ~StateFilter(AdminStates))
async def my_appointment(
    message: Message,
    state: FSMContext,
    appointment_uc: AppointmentUseCases,
    admin_ops_uc: AdminOpsUseCases,
    settings: Settings,
) -> None:
    perf = PerfSpan("client_my_appt")
    perf.mark("handler_entry")
    if message.from_user is None:
        return
    ap = appointment_uc.get_my_active_appointment(message.from_user.id)
    perf.mark("after_get_active_appointment")
    if ap is None or ap.status != AppointmentStatus.CONFIRMED:
        await message.answer(
            "Пока у вас нет действующей записи.\n"
            "Когда будете готовы, нажмите «Записаться».",
            reply_markup=main_menu_reply_keyboard(),
        )
        perf.mark("done_no_active")
        return

    builder = InlineKeyboardBuilder()
    builder.button(
        text="❌ Отменить запись",
        callback_data=build_client_cancel_my(ap.appointment_id),
    )
    builder.button(text="🏠 В меню", callback_data="c1|menu")
    builder.adjust(1)
    body = _my_appt_text(settings, ap, admin_ops_uc)
    perf.mark("after_build_my_appt_text")
    await message.answer(
        body,
        reply_markup=builder.as_markup(),
    )
    perf.mark("done_with_active")


@router.message(F.text == BTN_PRICE_LIST, ~StateFilter(AdminStates))
async def client_price_list(
    message: Message,
    admin_ops_uc: AdminOpsUseCases,
) -> None:
    text = _price_list_client_text(admin_ops_uc)
    await message.answer(text, reply_markup=main_menu_reply_keyboard())


@router.message(F.text == BTN_ADDRESS, ~StateFilter(AdminStates))
async def address_info(message: Message, settings: Settings, admin_ops_uc: AdminOpsUseCases) -> None:
    await message.answer(_contacts_text(settings, admin_ops_uc), reply_markup=main_menu_reply_keyboard())


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
    if await is_active_admin_fsm(state):
        try:
            await callback.answer(
                "Сначала завершите ввод в админке или откройте /admin.",
                show_alert=True,
            )
        except Exception:
            pass
        return
    await _safe_cq_answer(callback)
    if callback.data is None or callback.message is None:
        return
    try:
        action, parts = parse_client(callback.data)
    except ValueError:
        await callback.message.answer(
            "Действие устарело. Откройте раздел заново.",
            reply_markup=main_menu_reply_keyboard(),
        )
        return

    if action == "menu":
        await state.clear()
        await callback.message.answer(
            "Вы вернулись в клиентское меню.",
            reply_markup=main_menu_reply_keyboard(),
        )
        return

    if action == "book":
        perf = PerfSpan("client_inline_book")
        perf.mark("handler_entry")
        prev_data = await state.get_data()
        perf.mark("after_fsm_get_data")
        old_ui = prev_data.get(BOOKING_UI_MSG_ID_KEY)
        await state.clear()
        perf.mark("after_fsm_clear")
        if callback.from_user is None:
            return
        try:
            await invalidate_previous_booking_ui(callback.message.bot, callback.message.chat.id, old_ui)
        except Exception:
            pass
        perf.mark("after_invalidate_previous_ui")
        perf.mark("after_reply_keyboard_remove")
        try:
            draft = booking_uc.start_booking(user_id=callback.from_user.id)
        except ConflictError as e:
            await callback.message.answer(
                _start_booking_conflict_user_text(e),
                reply_markup=main_menu_reply_keyboard(),
            )
            perf.mark("done_conflict")
            return
        perf.mark("after_start_booking_uc_incl_cancel_others")
        await state.set_state(BookingStates.choose_service)
        perf.mark("after_set_state_choose_service")
        sent = await callback.message.answer(
            "Шаг 1/5 — выберите услугу",
            reply_markup=service_keyboard(
                draft.draft_id,
                booking_uc.list_available_services(),
                getattr(booking_uc, "service_catalog_repo", None),
            ),
        )
        perf.mark("after_answer_service_keyboard")
        await remember_booking_ui_message(state, sent)
        perf.mark("done")
        return

    if action == "x" and parts:
        ap_id = parts[0]
        if callback.from_user is None:
            return
        uid = callback.from_user.id
        try:
            _, already = appointment_uc.cancel_my_appointment_by_id(uid, ap_id)
        except AppError as e:
            await callback.message.answer(
                error_to_user_message(e),
                reply_markup=main_menu_reply_keyboard(),
            )
            return
        await state.clear()
        msg = (
            "Запись уже была отменена ранее."
            if already
            else "Запись отменена. При необходимости вы можете записаться снова."
        )
        await callback.message.answer(msg, reply_markup=main_menu_reply_keyboard())
        return

    await callback.message.answer(
        "Действие устарело. Откройте «Моя запись» снова.",
        reply_markup=main_menu_reply_keyboard(),
    )
