from aiogram.fsm.state import State, StatesGroup


class BookingStates(StatesGroup):
    choose_service = State()
    choose_date = State()
    choose_time = State()
    enter_contact = State()
    confirm = State()
    confirm_done = State()