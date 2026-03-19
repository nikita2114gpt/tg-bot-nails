from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Any
from uuid import uuid4

from app.domain.enums import (
    DraftStep,
    AppointmentStatus,
    OutboxType,
    OutboxStatus,
)


def generate_id() -> str:
    return uuid4().hex[:12]


def now_iso() -> str:
    return datetime.utcnow().isoformat()


@dataclass
class BookingDraft:
    draft_id: str = field(default_factory=generate_id)
    user_id: int = 0
    step: DraftStep = DraftStep.CHOOSE_SERVICE

    service_id: Optional[str] = None
    appointment_date: Optional[str] = None
    appointment_time: Optional[str] = None
    start_datetime_utc: Optional[str] = None

    customer_name: Optional[str] = None
    phone_e164: Optional[str] = None

    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)


@dataclass
class Appointment:
    appointment_id: str = field(default_factory=generate_id)
    draft_id: str = ""

    user_id: int = 0
    service_id: str = ""
    start_datetime_utc: str = ""

    customer_name: str = ""
    phone_e164: str = ""

    status: AppointmentStatus = AppointmentStatus.CONFIRMED

    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)


@dataclass
class OutboxEvent:
    event_id: str = field(default_factory=generate_id)

    event_type: OutboxType = OutboxType.ADMIN_NOTIFY
    status: OutboxStatus = OutboxStatus.PENDING

    idempotency_key: str = ""
    send_at: Optional[str] = None

    payload: dict[str, Any] = field(default_factory=dict)

    attempts: int = 0
    last_error: Optional[str] = None

    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)