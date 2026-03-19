from enum import Enum


class DraftStep(str, Enum):
    CHOOSE_SERVICE = "choose_service"
    CHOOSE_DATE = "choose_date"
    CHOOSE_TIME = "choose_time"
    ENTER_CONTACT = "enter_contact"
    CONFIRM = "confirm"
    CONFIRM_DONE = "confirm_done"
    CANCELLED = "cancelled"

    # Backward-compatible aliases (older code / stored JSON)
    choose_service = "choose_service"
    choose_date = "choose_date"
    choose_time = "choose_time"
    enter_contact = "enter_contact"
    confirm = "confirm"
    confirm_done = "confirm_done"
    cancelled = "cancelled"


class AppointmentStatus(str, Enum):
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


class OutboxType(str, Enum):
    ADMIN_NOTIFY = "admin_notify"
    REMINDER_CLIENT = "reminder_client"


class OutboxStatus(str, Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"