from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.presentation.callback.booking_callbacks import build_callback


def service_keyboard(draft_id: str, services: list[str]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    for service in services:
        builder.button(
            text=service,
            callback_data=build_callback("svc", draft_id, service),
        )

    builder.button(
        text="❌ Отмена",
        callback_data=build_callback("cancel", draft_id),
    )

    builder.adjust(1)
    return builder.as_markup()


def date_keyboard(draft_id: str, dates: list[str]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    for date_value in dates:
        builder.button(
            text=date_value,
            callback_data=build_callback("date", draft_id, date_value),
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


def time_keyboard(draft_id: str, time_slots: list[str]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    for time_value in time_slots:
        builder.button(
            text=time_value,
            callback_data=build_callback("time", draft_id, time_value),
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