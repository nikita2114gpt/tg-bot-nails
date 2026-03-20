from aiogram.fsm.state import State, StatesGroup


class BookingStates(StatesGroup):
    choose_service = State()
    choose_date = State()
    choose_time = State()
    enter_contact = State()
    confirm = State()
    confirm_done = State()


class AdminStates(StatesGroup):
    search_query = State()
    edit_name = State()
    edit_phone = State()
    schedule_open = State()
    schedule_close = State()
    schedule_step = State()
    service_add = State()
    service_edit_name = State()
    service_edit_duration = State()
    service_edit_price = State()
    blacklist_add = State()
    day_schedule_open = State()
    day_schedule_close = State()
    day_schedule_step = State()
    salon_address = State()
    salon_contacts = State()