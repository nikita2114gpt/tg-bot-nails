from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from uuid import uuid4


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


def _new_id() -> str:
    return uuid4().hex[:12]


@dataclass
class ScheduleSettings:
    open_time_hhmm: str = "0800"
    close_time_hhmm: str = "2000"
    slot_minutes: int = 60
    updated_at: str = field(default_factory=_now_iso)


@dataclass
class ServiceCatalogItem:
    service_id: str = field(default_factory=_new_id)
    name: str = ""
    duration_minutes: int = 60
    price_text: str = "—"
    is_active: bool = True
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)


@dataclass
class BlacklistEntry:
    entry_id: str = field(default_factory=_new_id)
    user_id: Optional[int] = None
    phone_e164: Optional[str] = None
    reason: Optional[str] = None
    is_active: bool = True
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)


@dataclass
class ClientLifecycleMarker:
    user_id: int = 0
    last_confirmed_at: Optional[str] = None
    last_confirmed_appointment_id: Optional[str] = None
    last_no_confirm_alert_appointment_id: Optional[str] = None
    last_reactivation_sent_at: Optional[str] = None
    # Последний номер, который клиент вводил в боте (для «Моя запись» при walk-in с тем же номером).
    last_phone_e164: Optional[str] = None
    updated_at: str = field(default_factory=_now_iso)


@dataclass
class DayScheduleOverride:
    date_yyyymmdd: str = ""
    is_closed: bool = False
    open_time_hhmm: Optional[str] = None
    close_time_hhmm: Optional[str] = None
    slot_minutes: Optional[int] = None
    updated_at: str = field(default_factory=_now_iso)


@dataclass
class SalonInfoSettings:
    address_text: str = ""
    contacts_text: str = ""
    show_address: bool = True
    show_contacts: bool = True
    updated_at: str = field(default_factory=_now_iso)
