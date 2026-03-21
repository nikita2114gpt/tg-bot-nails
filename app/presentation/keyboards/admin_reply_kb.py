"""Reply keyboard для администратора (отдельно от клиентского меню)."""

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

# Тексты кнопок не должны совпадать с client main_menu_kb.
BTN_ADMIN_HOME = "📋 Админ: меню"
BTN_ADMIN_TO_CLIENT = "Перейти в клиент меню"


def admin_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_ADMIN_HOME)],
            [KeyboardButton(text=BTN_ADMIN_TO_CLIENT)],
        ],
        resize_keyboard=True,
    )
