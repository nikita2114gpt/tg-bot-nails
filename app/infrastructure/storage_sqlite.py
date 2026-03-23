import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from app.domain.enums import AppointmentStatus, DraftStep, OutboxStatus, OutboxType
from app.domain.models import Appointment, BookingDraft, OutboxEvent
from app.domain.ops_models import (
    BlacklistEntry,
    ClientLifecycleMarker,
    DayScheduleOverride,
    PriceListItem,
    SalonInfoSettings,
    ScheduleSettings,
    ServiceCatalogItem,
)


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


def _parse_enum(enum_cls: type[Any], raw: Any, default: Any) -> Any:
    try:
        if isinstance(raw, enum_cls):
            return raw
        return enum_cls(raw)
    except Exception:
        return default


def _parse_payload(payload_text: Optional[str]) -> dict[str, Any]:
    if payload_text is None:
        return {}
    if not isinstance(payload_text, str) or not payload_text:
        return {}
    try:
        value = json.loads(payload_text)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _dump_payload(payload: Any) -> str:
    if not isinstance(payload, dict):
        payload = {}
    return json.dumps(payload, ensure_ascii=False)


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    # Safer defaults for concurrent readers/writers.
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS drafts (
            draft_id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            step TEXT NOT NULL,
            service_id TEXT,
            appointment_date TEXT,
            appointment_time TEXT,
            start_datetime_utc TEXT,
            customer_name TEXT,
            phone_e164 TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_drafts_user_updated
        ON drafts(user_id, updated_at DESC, created_at DESC);
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS appointments (
            appointment_id TEXT PRIMARY KEY,
            draft_id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            service_id TEXT NOT NULL,
            start_datetime_utc TEXT NOT NULL,
            customer_name TEXT NOT NULL,
            phone_e164 TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_appointments_draft_id
        ON appointments(draft_id);
        """
    )

    # Slot-level uniqueness for active records:
    # only CONFIRMED appointments block a slot.
    # Legacy full-slot unique index is dropped to avoid false conflicts
    # from CANCELLED rows that keep the same start_datetime_utc.
    try:
        conn.execute("DROP INDEX IF EXISTS uq_appointments_slot;")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_appointments_slot_confirmed
            ON appointments(start_datetime_utc)
            WHERE status='confirmed';
            """
        )
    except sqlite3.IntegrityError:
        pass
    except sqlite3.OperationalError:
        pass

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS outbox (
            event_id TEXT NOT NULL UNIQUE,
            event_type TEXT NOT NULL,
            status TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            send_at TEXT,
            payload TEXT,
            attempts INTEGER NOT NULL,
            last_error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_outbox_status_send
        ON outbox(status, send_at);
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schedule_settings (
            id INTEGER PRIMARY KEY CHECK (id=1),
            open_time_hhmm TEXT NOT NULL,
            close_time_hhmm TEXT NOT NULL,
            slot_minutes INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS day_schedule_overrides (
            date_yyyymmdd TEXT PRIMARY KEY,
            is_closed INTEGER NOT NULL,
            open_time_hhmm TEXT,
            close_time_hhmm TEXT,
            slot_minutes INTEGER,
            updated_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS salon_info_settings (
            id INTEGER PRIMARY KEY CHECK (id=1),
            address_text TEXT NOT NULL,
            contacts_text TEXT NOT NULL,
            show_address INTEGER NOT NULL,
            show_contacts INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS service_catalog (
            service_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            duration_minutes INTEGER NOT NULL,
            price_text TEXT NOT NULL,
            is_active INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_service_catalog_active
        ON service_catalog(is_active, name);
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS blacklist_entries (
            entry_id TEXT PRIMARY KEY,
            user_id INTEGER,
            phone_e164 TEXT,
            reason TEXT,
            is_active INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_blacklist_user_active
        ON blacklist_entries(user_id, is_active, created_at DESC);
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_blacklist_phone_active
        ON blacklist_entries(phone_e164, is_active, created_at DESC);
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS client_lifecycle_markers (
            user_id INTEGER PRIMARY KEY,
            last_confirmed_at TEXT,
            last_confirmed_appointment_id TEXT,
            last_no_confirm_alert_appointment_id TEXT,
            last_reactivation_sent_at TEXT,
            updated_at TEXT NOT NULL
        );
        """
    )
    try:
        conn.execute(
            "ALTER TABLE client_lifecycle_markers ADD COLUMN last_confirmed_appointment_id TEXT;"
        )
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(
            "ALTER TABLE client_lifecycle_markers ADD COLUMN last_no_confirm_alert_appointment_id TEXT;"
        )
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(
            "ALTER TABLE client_lifecycle_markers ADD COLUMN last_phone_e164 TEXT;"
        )
    except sqlite3.OperationalError:
        pass

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS price_list_items (
            item_id TEXT PRIMARY KEY,
            display_text TEXT NOT NULL,
            is_active INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_price_list_active_created
        ON price_list_items(is_active, created_at ASC);
        """
    )


def _draft_from_row(row: sqlite3.Row) -> Optional[BookingDraft]:
    if row is None:
        return None

    step = _parse_enum(DraftStep, row["step"], DraftStep.CANCELLED)

    created_at = row["created_at"] if isinstance(row["created_at"], str) else _now_iso()
    updated_at = row["updated_at"] if isinstance(row["updated_at"], str) else _now_iso()

    return BookingDraft(
        draft_id=row["draft_id"],
        user_id=int(row["user_id"]),
        step=step,
        service_id=row["service_id"],
        appointment_date=row["appointment_date"],
        appointment_time=row["appointment_time"],
        start_datetime_utc=row["start_datetime_utc"],
        customer_name=row["customer_name"],
        phone_e164=row["phone_e164"],
        created_at=created_at,
        updated_at=updated_at,
    )


def _appointment_from_row(row: sqlite3.Row) -> Optional[Appointment]:
    if row is None:
        return None

    status = _parse_enum(AppointmentStatus, row["status"], AppointmentStatus.CANCELLED)

    created_at = row["created_at"] if isinstance(row["created_at"], str) else _now_iso()
    updated_at = row["updated_at"] if isinstance(row["updated_at"], str) else _now_iso()

    return Appointment(
        appointment_id=row["appointment_id"],
        draft_id=row["draft_id"],
        user_id=int(row["user_id"]),
        service_id=row["service_id"] or "",
        start_datetime_utc=row["start_datetime_utc"] or "",
        customer_name=row["customer_name"] or "",
        phone_e164=row["phone_e164"] or "",
        status=status,
        created_at=created_at,
        updated_at=updated_at,
    )


def _outbox_from_row(row: sqlite3.Row) -> Optional[OutboxEvent]:
    if row is None:
        return None

    # Keep legacy semantics:
    # - invalid event_type => force status=FAILED (see storage_filejson._outbox_from_dict)
    event_type_raw = row["event_type"]
    type_valid = True
    try:
        event_type = OutboxType(event_type_raw)
    except Exception:
        type_valid = False
        event_type = OutboxType.ADMIN_NOTIFY

    try:
        status = OutboxStatus(row["status"])
    except Exception:
        status = OutboxStatus.FAILED

    if not type_valid:
        status = OutboxStatus.FAILED

    created_at = row["created_at"] if isinstance(row["created_at"], str) else _now_iso()
    updated_at = row["updated_at"] if isinstance(row["updated_at"], str) else _now_iso()

    payload = _parse_payload(row["payload"])

    send_at = row["send_at"] if isinstance(row["send_at"], str) else None

    return OutboxEvent(
        event_id=row["event_id"],
        event_type=event_type,
        status=status,
        idempotency_key=row["idempotency_key"],
        send_at=send_at,
        payload=payload,
        attempts=int(row["attempts"]) if row["attempts"] is not None else 0,
        last_error=row["last_error"] if isinstance(row["last_error"], str) else None,
        created_at=created_at,
        updated_at=updated_at,
    )


def _schedule_settings_from_row(row: sqlite3.Row) -> Optional[ScheduleSettings]:
    if row is None:
        return None
    try:
        slot_minutes = int(row["slot_minutes"])
    except Exception:
        slot_minutes = 60
    updated_at = row["updated_at"] if isinstance(row["updated_at"], str) else _now_iso()
    return ScheduleSettings(
        open_time_hhmm=row["open_time_hhmm"] or "0800",
        close_time_hhmm=row["close_time_hhmm"] or "2000",
        slot_minutes=slot_minutes,
        updated_at=updated_at,
    )


def _day_override_from_row(row: sqlite3.Row) -> Optional[DayScheduleOverride]:
    if row is None:
        return None
    slot_minutes = None
    if row["slot_minutes"] is not None:
        try:
            slot_minutes = int(row["slot_minutes"])
        except Exception:
            slot_minutes = None
    updated_at = row["updated_at"] if isinstance(row["updated_at"], str) else _now_iso()
    return DayScheduleOverride(
        date_yyyymmdd=row["date_yyyymmdd"] or "",
        is_closed=bool(int(row["is_closed"])) if row["is_closed"] is not None else False,
        open_time_hhmm=row["open_time_hhmm"] if isinstance(row["open_time_hhmm"], str) else None,
        close_time_hhmm=row["close_time_hhmm"] if isinstance(row["close_time_hhmm"], str) else None,
        slot_minutes=slot_minutes,
        updated_at=updated_at,
    )


def _salon_info_from_row(row: sqlite3.Row) -> Optional[SalonInfoSettings]:
    if row is None:
        return None
    updated_at = row["updated_at"] if isinstance(row["updated_at"], str) else _now_iso()
    return SalonInfoSettings(
        address_text=row["address_text"] if isinstance(row["address_text"], str) else "",
        contacts_text=row["contacts_text"] if isinstance(row["contacts_text"], str) else "",
        show_address=bool(int(row["show_address"])) if row["show_address"] is not None else True,
        show_contacts=bool(int(row["show_contacts"])) if row["show_contacts"] is not None else True,
        updated_at=updated_at,
    )


def _service_item_from_row(row: sqlite3.Row) -> Optional[ServiceCatalogItem]:
    if row is None:
        return None
    try:
        duration_minutes = int(row["duration_minutes"])
    except Exception:
        duration_minutes = 60
    created_at = row["created_at"] if isinstance(row["created_at"], str) else _now_iso()
    updated_at = row["updated_at"] if isinstance(row["updated_at"], str) else _now_iso()
    return ServiceCatalogItem(
        service_id=row["service_id"] or "",
        name=row["name"] or "",
        duration_minutes=duration_minutes,
        price_text=row["price_text"] or "—",
        is_active=bool(int(row["is_active"])) if row["is_active"] is not None else True,
        created_at=created_at,
        updated_at=updated_at,
    )


def _blacklist_from_row(row: sqlite3.Row) -> Optional[BlacklistEntry]:
    if row is None:
        return None
    user_id = None
    if row["user_id"] is not None:
        try:
            user_id = int(row["user_id"])
        except Exception:
            user_id = None
    phone_e164 = row["phone_e164"] if isinstance(row["phone_e164"], str) else None
    reason = row["reason"] if isinstance(row["reason"], str) else None
    created_at = row["created_at"] if isinstance(row["created_at"], str) else _now_iso()
    updated_at = row["updated_at"] if isinstance(row["updated_at"], str) else _now_iso()
    return BlacklistEntry(
        entry_id=row["entry_id"] or "",
        user_id=user_id,
        phone_e164=phone_e164,
        reason=reason,
        is_active=bool(int(row["is_active"])) if row["is_active"] is not None else True,
        created_at=created_at,
        updated_at=updated_at,
    )


def _lifecycle_marker_from_row(row: sqlite3.Row) -> Optional[ClientLifecycleMarker]:
    if row is None:
        return None
    try:
        user_id = int(row["user_id"])
    except Exception:
        return None
    last_confirmed_at = (
        row["last_confirmed_at"] if isinstance(row["last_confirmed_at"], str) else None
    )
    last_confirmed_appointment_id = (
        row["last_confirmed_appointment_id"]
        if isinstance(row["last_confirmed_appointment_id"], str)
        else None
    )
    last_no_confirm_alert_appointment_id = (
        row["last_no_confirm_alert_appointment_id"]
        if isinstance(row["last_no_confirm_alert_appointment_id"], str)
        else None
    )
    last_reactivation_sent_at = (
        row["last_reactivation_sent_at"]
        if isinstance(row["last_reactivation_sent_at"], str)
        else None
    )
    updated_at = row["updated_at"] if isinstance(row["updated_at"], str) else _now_iso()
    try:
        lph = row["last_phone_e164"]
    except (KeyError, IndexError):
        lph = None
    last_phone_e164 = lph if isinstance(lph, str) and lph.strip() else None
    return ClientLifecycleMarker(
        user_id=user_id,
        last_confirmed_at=last_confirmed_at,
        last_confirmed_appointment_id=last_confirmed_appointment_id,
        last_no_confirm_alert_appointment_id=last_no_confirm_alert_appointment_id,
        last_reactivation_sent_at=last_reactivation_sent_at,
        last_phone_e164=last_phone_e164,
        updated_at=updated_at,
    )


class DraftRepository:
    def __init__(self, db_path: str | Path):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with _connect(self._db_path) as conn:
            _init_schema(conn)

    def save_draft(self, draft: BookingDraft) -> None:
        with _connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO drafts (
                    draft_id,
                    user_id,
                    step,
                    service_id,
                    appointment_date,
                    appointment_time,
                    start_datetime_utc,
                    customer_name,
                    phone_e164,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(draft_id) DO UPDATE SET
                    user_id=excluded.user_id,
                    step=excluded.step,
                    service_id=excluded.service_id,
                    appointment_date=excluded.appointment_date,
                    appointment_time=excluded.appointment_time,
                    start_datetime_utc=excluded.start_datetime_utc,
                    customer_name=excluded.customer_name,
                    phone_e164=excluded.phone_e164,
                    created_at=excluded.created_at,
                    updated_at=excluded.updated_at
                """,
                (
                    draft.draft_id,
                    draft.user_id,
                    draft.step.value,
                    draft.service_id,
                    draft.appointment_date,
                    draft.appointment_time,
                    draft.start_datetime_utc,
                    draft.customer_name,
                    draft.phone_e164,
                    draft.created_at,
                    draft.updated_at,
                ),
            )

    def get_by_id(self, draft_id: str) -> Optional[BookingDraft]:
        with _connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT * FROM drafts WHERE draft_id=? LIMIT 1", (draft_id,)
            ).fetchone()
            return _draft_from_row(row)

    def get_by_user_id(self, user_id: int) -> Optional[BookingDraft]:
        # JSON legacy uses "last element behavior" (array append + reversed()).
        # We approximate it by selecting the newest updated_at.
        with _connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT * FROM drafts
                WHERE user_id=?
                ORDER BY updated_at DESC, created_at DESC
                LIMIT 1
                """,
                (user_id,),
            ).fetchone()
            return _draft_from_row(row)

    def cancel_other_drafts_for_user(self, user_id: int, keep_draft_id: str) -> None:
        """Помечает остальные черновики пользователя как отменённые (новый сценарий записи)."""
        now = _now_iso()
        with _connect(self._db_path) as conn:
            conn.execute(
                """
                UPDATE drafts SET step=?, updated_at=?
                WHERE user_id=? AND draft_id!=? AND step!=?
                """,
                (
                    DraftStep.CANCELLED.value,
                    now,
                    user_id,
                    keep_draft_id,
                    DraftStep.CANCELLED.value,
                ),
            )


class AppointmentRepository:
    def __init__(self, db_path: str | Path):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with _connect(self._db_path) as conn:
            _init_schema(conn)

    def save_appointment(self, appointment: Appointment) -> None:
        with _connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO appointments (
                    appointment_id,
                    draft_id,
                    user_id,
                    service_id,
                    start_datetime_utc,
                    customer_name,
                    phone_e164,
                    status,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(appointment_id) DO UPDATE SET
                    draft_id=excluded.draft_id,
                    user_id=excluded.user_id,
                    service_id=excluded.service_id,
                    start_datetime_utc=excluded.start_datetime_utc,
                    customer_name=excluded.customer_name,
                    phone_e164=excluded.phone_e164,
                    status=excluded.status,
                    created_at=excluded.created_at,
                    updated_at=excluded.updated_at
                """,
                (
                    appointment.appointment_id,
                    appointment.draft_id,
                    appointment.user_id,
                    appointment.service_id,
                    appointment.start_datetime_utc,
                    appointment.customer_name,
                    appointment.phone_e164,
                    appointment.status.value,
                    appointment.created_at,
                    appointment.updated_at,
                ),
            )
            if appointment.status == AppointmentStatus.CANCELLED:
                self._trim_cancelled_overflow(conn)

    def _trim_cancelled_overflow(self, conn: sqlite3.Connection) -> None:
        """Не более 500 отменённых записей: удаляем самые старые по created_at."""
        row = conn.execute(
            "SELECT COUNT(*) FROM appointments WHERE status=?",
            (AppointmentStatus.CANCELLED.value,),
        ).fetchone()
        n = int(row[0]) if row else 0
        if n <= 500:
            return
        to_delete = n - 500
        old_rows = conn.execute(
            """
            SELECT appointment_id FROM appointments
            WHERE status=?
            ORDER BY created_at ASC, appointment_id ASC
            LIMIT ?
            """,
            (AppointmentStatus.CANCELLED.value, to_delete),
        ).fetchall()
        for r in old_rows:
            aid = r["appointment_id"] if hasattr(r, "keys") else r[0]
            conn.execute(
                "DELETE FROM appointments WHERE appointment_id=?",
                (aid,),
            )

    def get_by_draft_id(self, draft_id: str) -> Optional[Appointment]:
        # JSON legacy returns the first matching appointment (array order).
        # If there are duplicates, JSON tends to keep the oldest.
        with _connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT * FROM appointments
                WHERE draft_id=?
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (draft_id,),
            ).fetchone()
            return _appointment_from_row(row)

    def get_by_start_datetime_utc(
        self, start_datetime_utc: str
    ) -> Optional[Appointment]:
        with _connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT * FROM appointments
                WHERE start_datetime_utc=? AND status=?
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (start_datetime_utc, AppointmentStatus.CONFIRMED.value),
            ).fetchone()
            return _appointment_from_row(row)

    def get_by_id(self, appointment_id: str) -> Optional[Appointment]:
        with _connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT * FROM appointments WHERE appointment_id=? LIMIT 1",
                (appointment_id,),
            ).fetchone()
            return _appointment_from_row(row)

    def list_all(self) -> list[Appointment]:
        with _connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT * FROM appointments
                ORDER BY created_at ASC
                """
            ).fetchall()
            result: list[Appointment] = []
            for row in rows:
                ap = _appointment_from_row(row)
                if ap is not None:
                    result.append(ap)
            return result

    def list_by_user_id(self, user_id: int) -> list[Appointment]:
        with _connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT * FROM appointments
                WHERE user_id=?
                ORDER BY start_datetime_utc ASC, created_at ASC
                """,
                (user_id,),
            ).fetchall()
            result: list[Appointment] = []
            for row in rows:
                ap = _appointment_from_row(row)
                if ap is not None:
                    result.append(ap)
            return result

    def get_active_confirmed_for_user(self, user_id: int) -> Optional[Appointment]:
        """
        Активная запись: CONFIRMED, start_datetime_utc >= now (UTC, формат %Y%m%dT%H%M),
        ближайшая по слоту; при тай-брейке — appointment_id ASC.
        """
        now_key = datetime.utcnow().strftime("%Y%m%dT%H%M")
        with _connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT * FROM appointments
                WHERE user_id=? AND status=? AND start_datetime_utc >= ?
                ORDER BY start_datetime_utc ASC, appointment_id ASC
                LIMIT 1
                """,
                (user_id, AppointmentStatus.CONFIRMED.value, now_key),
            ).fetchone()
            return _appointment_from_row(row)

    def list_by_status(self, status: AppointmentStatus) -> list[Appointment]:
        with _connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT * FROM appointments
                WHERE status=?
                ORDER BY start_datetime_utc ASC, created_at ASC
                """,
                (status.value,),
            ).fetchall()
            result: list[Appointment] = []
            for row in rows:
                ap = _appointment_from_row(row)
                if ap is not None:
                    result.append(ap)
            return result

    def list_starting_with_date(self, date_yyyymmdd: str) -> list[Appointment]:
        prefix = f"{date_yyyymmdd.strip()}%"
        with _connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT * FROM appointments
                WHERE start_datetime_utc LIKE ?
                ORDER BY start_datetime_utc ASC, created_at ASC
                """,
                (prefix,),
            ).fetchall()
            result: list[Appointment] = []
            for row in rows:
                ap = _appointment_from_row(row)
                if ap is not None:
                    result.append(ap)
            return result

    def search_appointments(
        self,
        name_substr: Optional[str] = None,
        phone_substr: Optional[str] = None,
        date_yyyymmdd: Optional[str] = None,
    ) -> list[Appointment]:
        name_substr = (name_substr or "").strip()
        phone_substr = (phone_substr or "").strip()
        date_yyyymmdd = (date_yyyymmdd or "").strip()

        clauses: list[str] = []
        params: list[Any] = []
        if name_substr:
            clauses.append("LOWER(customer_name) LIKE ?")
            params.append(f"%{name_substr.lower()}%")
        if phone_substr:
            clauses.append("LOWER(phone_e164) LIKE ?")
            params.append(f"%{phone_substr.lower()}%")
        if date_yyyymmdd:
            clauses.append("start_datetime_utc LIKE ?")
            params.append(f"{date_yyyymmdd}%")

        where_sql = " AND ".join(clauses) if clauses else "1=1"
        with _connect(self._db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM appointments
                WHERE {where_sql}
                ORDER BY start_datetime_utc ASC, created_at ASC
                """,
                params,
            ).fetchall()
            result: list[Appointment] = []
            for row in rows:
                ap = _appointment_from_row(row)
                if ap is not None:
                    result.append(ap)
            return result

    def confirm_booking_atomic(
        self,
        draft_id: str,
        appointment_to_create: Appointment,
        outbox_events: list[OutboxEvent],
    ) -> Appointment:
        conn = _connect(self._db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT * FROM appointments
                WHERE draft_id=?
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (draft_id,),
            ).fetchone()
            existing = _appointment_from_row(row)
            if existing is not None:
                appointment = existing
            else:
                conn.execute(
                    """
                    INSERT INTO appointments (
                        appointment_id,
                        draft_id,
                        user_id,
                        service_id,
                        start_datetime_utc,
                        customer_name,
                        phone_e164,
                        status,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        appointment_to_create.appointment_id,
                        draft_id,
                        appointment_to_create.user_id,
                        appointment_to_create.service_id,
                        appointment_to_create.start_datetime_utc,
                        appointment_to_create.customer_name,
                        appointment_to_create.phone_e164,
                        appointment_to_create.status.value,
                        appointment_to_create.created_at,
                        appointment_to_create.updated_at,
                    ),
                )
                appointment = appointment_to_create

            for event in outbox_events:
                conn.execute(
                    """
                    INSERT INTO outbox (
                        event_id,
                        event_type,
                        status,
                        idempotency_key,
                        send_at,
                        payload,
                        attempts,
                        last_error,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(idempotency_key) DO UPDATE SET
                        event_id=excluded.event_id,
                        event_type=excluded.event_type,
                        status=excluded.status,
                        send_at=excluded.send_at,
                        payload=excluded.payload,
                        attempts=excluded.attempts,
                        last_error=excluded.last_error,
                        created_at=excluded.created_at,
                        updated_at=excluded.updated_at
                    """,
                    (
                        event.event_id,
                        event.event_type.value,
                        event.status.value,
                        event.idempotency_key,
                        event.send_at,
                        _dump_payload(event.payload),
                        event.attempts,
                        event.last_error,
                        event.created_at,
                        event.updated_at,
                    ),
                )

            conn.execute(
                """
                UPDATE drafts
                SET step=?,
                    updated_at=?
                WHERE draft_id=?
                """,
                (DraftStep.CONFIRM_DONE.value, _now_iso(), draft_id),
            )

            conn.commit()
            return appointment
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()


class OutboxRepository:
    def __init__(self, db_path: str | Path):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with _connect(self._db_path) as conn:
            _init_schema(conn)

    def save_event(self, event: OutboxEvent) -> None:
        with _connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO outbox (
                    event_id,
                    event_type,
                    status,
                    idempotency_key,
                    send_at,
                    payload,
                    attempts,
                    last_error,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(idempotency_key) DO UPDATE SET
                    event_id=excluded.event_id,
                    event_type=excluded.event_type,
                    status=excluded.status,
                    send_at=excluded.send_at,
                    payload=excluded.payload,
                    attempts=excluded.attempts,
                    last_error=excluded.last_error,
                    created_at=excluded.created_at,
                    updated_at=excluded.updated_at
                """,
                (
                    event.event_id,
                    event.event_type.value,
                    event.status.value,
                    event.idempotency_key,
                    event.send_at,
                    _dump_payload(event.payload),
                    event.attempts,
                    event.last_error,
                    event.created_at,
                    event.updated_at,
                ),
            )

    def get_by_idempotency_key(self, key: str) -> Optional[OutboxEvent]:
        with _connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT * FROM outbox WHERE idempotency_key=? LIMIT 1", (key,)
            ).fetchone()
            return _outbox_from_row(row)

    def list_pending(self) -> list[OutboxEvent]:
        with _connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT * FROM outbox
                WHERE status=?
                ORDER BY send_at IS NULL DESC, send_at ASC, created_at ASC
                """,
                (OutboxStatus.PENDING.value,),
            ).fetchall()
            result: list[OutboxEvent] = []
            for row in rows:
                ev = _outbox_from_row(row)
                if ev is not None:
                    result.append(ev)
            return result

    def mark_sent(self, event_id: str) -> None:
        with _connect(self._db_path) as conn:
            conn.execute(
                """
                UPDATE outbox
                SET status=?,
                    updated_at=?
                WHERE event_id=?
                """,
                (OutboxStatus.SENT.value, _now_iso(), event_id),
            )

    def mark_failed(self, event_id: str, error_message: str) -> None:
        with _connect(self._db_path) as conn:
            conn.execute(
                """
                UPDATE outbox
                SET status=?,
                    last_error=?,
                    updated_at=?
                WHERE event_id=?
                """,
                (OutboxStatus.FAILED.value, error_message, _now_iso(), event_id),
            )


class ScheduleSettingsRepository:
    def __init__(self, db_path: str | Path):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with _connect(self._db_path) as conn:
            _init_schema(conn)

    def get(self) -> ScheduleSettings:
        with _connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT * FROM schedule_settings WHERE id=1 LIMIT 1"
            ).fetchone()
            parsed = _schedule_settings_from_row(row)
            if parsed is not None:
                return parsed
        return ScheduleSettings()

    def save(self, settings: ScheduleSettings) -> None:
        with _connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO schedule_settings (id, open_time_hhmm, close_time_hhmm, slot_minutes, updated_at)
                VALUES (1, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    open_time_hhmm=excluded.open_time_hhmm,
                    close_time_hhmm=excluded.close_time_hhmm,
                    slot_minutes=excluded.slot_minutes,
                    updated_at=excluded.updated_at
                """,
                (
                    settings.open_time_hhmm,
                    settings.close_time_hhmm,
                    settings.slot_minutes,
                    settings.updated_at,
                ),
            )


class DayScheduleOverrideRepository:
    def __init__(self, db_path: str | Path):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with _connect(self._db_path) as conn:
            _init_schema(conn)

    def get_by_date(self, date_yyyymmdd: str) -> Optional[DayScheduleOverride]:
        with _connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT * FROM day_schedule_overrides WHERE date_yyyymmdd=? LIMIT 1",
                (date_yyyymmdd,),
            ).fetchone()
            return _day_override_from_row(row)

    def list_all(self) -> list[DayScheduleOverride]:
        with _connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM day_schedule_overrides ORDER BY date_yyyymmdd ASC"
            ).fetchall()
            result: list[DayScheduleOverride] = []
            for row in rows:
                item = _day_override_from_row(row)
                if item is not None:
                    result.append(item)
            return result

    def save(self, item: DayScheduleOverride) -> None:
        with _connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO day_schedule_overrides (
                    date_yyyymmdd, is_closed, open_time_hhmm, close_time_hhmm, slot_minutes, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(date_yyyymmdd) DO UPDATE SET
                    is_closed=excluded.is_closed,
                    open_time_hhmm=excluded.open_time_hhmm,
                    close_time_hhmm=excluded.close_time_hhmm,
                    slot_minutes=excluded.slot_minutes,
                    updated_at=excluded.updated_at
                """,
                (
                    item.date_yyyymmdd,
                    1 if item.is_closed else 0,
                    item.open_time_hhmm,
                    item.close_time_hhmm,
                    item.slot_minutes,
                    item.updated_at,
                ),
            )


class SalonInfoSettingsRepository:
    def __init__(self, db_path: str | Path):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with _connect(self._db_path) as conn:
            _init_schema(conn)

    def get(self) -> Optional[SalonInfoSettings]:
        with _connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT * FROM salon_info_settings WHERE id=1 LIMIT 1"
            ).fetchone()
            return _salon_info_from_row(row)

    def save(self, value: SalonInfoSettings) -> None:
        with _connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO salon_info_settings (
                    id, address_text, contacts_text, show_address, show_contacts, updated_at
                )
                VALUES (1, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    address_text=excluded.address_text,
                    contacts_text=excluded.contacts_text,
                    show_address=excluded.show_address,
                    show_contacts=excluded.show_contacts,
                    updated_at=excluded.updated_at
                """,
                (
                    value.address_text,
                    value.contacts_text,
                    1 if value.show_address else 0,
                    1 if value.show_contacts else 0,
                    value.updated_at,
                ),
            )


class ServiceCatalogRepository:
    def __init__(self, db_path: str | Path):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with _connect(self._db_path) as conn:
            _init_schema(conn)

    def save_item(self, item: ServiceCatalogItem) -> None:
        with _connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO service_catalog (
                    service_id, name, duration_minutes, price_text, is_active, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(service_id) DO UPDATE SET
                    name=excluded.name,
                    duration_minutes=excluded.duration_minutes,
                    price_text=excluded.price_text,
                    is_active=excluded.is_active,
                    created_at=excluded.created_at,
                    updated_at=excluded.updated_at
                """,
                (
                    item.service_id,
                    item.name,
                    item.duration_minutes,
                    item.price_text,
                    1 if item.is_active else 0,
                    item.created_at,
                    item.updated_at,
                ),
            )

    def get_by_id(self, service_id: str) -> Optional[ServiceCatalogItem]:
        with _connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT * FROM service_catalog WHERE service_id=? LIMIT 1",
                (service_id,),
            ).fetchone()
            return _service_item_from_row(row)

    def list_all(self) -> list[ServiceCatalogItem]:
        with _connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM service_catalog ORDER BY is_active DESC, name ASC"
            ).fetchall()
            result: list[ServiceCatalogItem] = []
            for row in rows:
                item = _service_item_from_row(row)
                if item is not None:
                    result.append(item)
            return result

    def delete_item(self, service_id: str) -> None:
        with _connect(self._db_path) as conn:
            conn.execute(
                "DELETE FROM service_catalog WHERE service_id=?",
                (service_id,),
            )


class BlacklistRepository:
    def __init__(self, db_path: str | Path):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with _connect(self._db_path) as conn:
            _init_schema(conn)

    def save_entry(self, entry: BlacklistEntry) -> None:
        with _connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO blacklist_entries (
                    entry_id, user_id, phone_e164, reason, is_active, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(entry_id) DO UPDATE SET
                    user_id=excluded.user_id,
                    phone_e164=excluded.phone_e164,
                    reason=excluded.reason,
                    is_active=excluded.is_active,
                    created_at=excluded.created_at,
                    updated_at=excluded.updated_at
                """,
                (
                    entry.entry_id,
                    entry.user_id,
                    entry.phone_e164,
                    entry.reason,
                    1 if entry.is_active else 0,
                    entry.created_at,
                    entry.updated_at,
                ),
            )

    def get_active_by_user_id(self, user_id: int) -> Optional[BlacklistEntry]:
        with _connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT * FROM blacklist_entries
                WHERE user_id=? AND is_active=1
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (user_id,),
            ).fetchone()
            return _blacklist_from_row(row)

    def get_active_by_phone(self, phone_e164: str) -> Optional[BlacklistEntry]:
        with _connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT * FROM blacklist_entries
                WHERE phone_e164=? AND is_active=1
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (phone_e164,),
            ).fetchone()
            return _blacklist_from_row(row)

    def list_all(self) -> list[BlacklistEntry]:
        with _connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT * FROM blacklist_entries
                ORDER BY is_active DESC, created_at DESC
                """
            ).fetchall()
            result: list[BlacklistEntry] = []
            for row in rows:
                entry = _blacklist_from_row(row)
                if entry is not None:
                    result.append(entry)
            return result


class ClientLifecycleMarkerRepository:
    def __init__(self, db_path: str | Path):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with _connect(self._db_path) as conn:
            _init_schema(conn)

    def get_by_user_id(self, user_id: int) -> Optional[ClientLifecycleMarker]:
        with _connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT * FROM client_lifecycle_markers
                WHERE user_id=?
                LIMIT 1
                """,
                (user_id,),
            ).fetchone()
            return _lifecycle_marker_from_row(row)

    def save(self, marker: ClientLifecycleMarker) -> None:
        with _connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO client_lifecycle_markers (
                    user_id,
                    last_confirmed_at,
                    last_confirmed_appointment_id,
                    last_no_confirm_alert_appointment_id,
                    last_reactivation_sent_at,
                    last_phone_e164,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    last_confirmed_at=excluded.last_confirmed_at,
                    last_confirmed_appointment_id=excluded.last_confirmed_appointment_id,
                    last_no_confirm_alert_appointment_id=excluded.last_no_confirm_alert_appointment_id,
                    last_reactivation_sent_at=excluded.last_reactivation_sent_at,
                    last_phone_e164=excluded.last_phone_e164,
                    updated_at=excluded.updated_at
                """,
                (
                    marker.user_id,
                    marker.last_confirmed_at,
                    marker.last_confirmed_appointment_id,
                    marker.last_no_confirm_alert_appointment_id,
                    marker.last_reactivation_sent_at,
                    marker.last_phone_e164,
                    marker.updated_at,
                ),
            )


def _price_item_from_row(row: sqlite3.Row | None) -> Optional[PriceListItem]:
    if row is None:
        return None
    return PriceListItem(
        item_id=row["item_id"],
        display_text=row["display_text"] or "",
        is_active=bool(int(row["is_active"] or 0)),
        created_at=row["created_at"] if isinstance(row["created_at"], str) else _now_iso(),
        updated_at=row["updated_at"] if isinstance(row["updated_at"], str) else _now_iso(),
    )


class PriceListRepository:
    def __init__(self, db_path: str | Path):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with _connect(self._db_path) as conn:
            _init_schema(conn)

    def list_all(self) -> list[PriceListItem]:
        with _connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT * FROM price_list_items
                ORDER BY created_at DESC, item_id DESC
                """
            ).fetchall()
            out: list[PriceListItem] = []
            for row in rows:
                item = _price_item_from_row(row)
                if item is not None:
                    out.append(item)
            return out

    def list_active(self) -> list[PriceListItem]:
        with _connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT * FROM price_list_items
                WHERE is_active=1
                ORDER BY created_at ASC, item_id ASC
                """
            ).fetchall()
            out: list[PriceListItem] = []
            for row in rows:
                item = _price_item_from_row(row)
                if item is not None:
                    out.append(item)
            return out

    def get_by_id(self, item_id: str) -> Optional[PriceListItem]:
        with _connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT * FROM price_list_items WHERE item_id=? LIMIT 1",
                (item_id,),
            ).fetchone()
            return _price_item_from_row(row)

    def save(self, item: PriceListItem) -> None:
        with _connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO price_list_items (
                    item_id, display_text, is_active, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(item_id) DO UPDATE SET
                    display_text=excluded.display_text,
                    is_active=excluded.is_active,
                    created_at=excluded.created_at,
                    updated_at=excluded.updated_at
                """,
                (
                    item.item_id,
                    item.display_text,
                    1 if item.is_active else 0,
                    item.created_at,
                    item.updated_at,
                ),
            )

    def delete(self, item_id: str) -> None:
        with _connect(self._db_path) as conn:
            conn.execute(
                "DELETE FROM price_list_items WHERE item_id=?",
                (item_id,),
            )

