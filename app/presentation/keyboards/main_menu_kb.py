from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

BTN_BOOK = "📅 Записаться"
BTN_MY_APPT = "📋 Моя запись"
BTN_SERVICES_INFO = "💳 Услуги и цены"
BTN_ADDRESS = "📍 Адрес / Контакты"
BTN_MAIN_MENU = "🏠 В меню"


def main_menu_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_BOOK)],
            [KeyboardButton(text=BTN_MY_APPT)],
            [
                KeyboardButton(text=BTN_SERVICES_INFO),
                KeyboardButton(text=BTN_ADDRESS),
            ],
        ],
        resize_keyboard=True,
    )


def post_confirm_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=BTN_MY_APPT),
                KeyboardButton(text=BTN_MAIN_MENU),
            ],
        ],
        resize_keyboard=True,
    )
