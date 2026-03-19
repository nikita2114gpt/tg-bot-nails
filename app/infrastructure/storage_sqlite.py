import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from app.domain.enums import AppointmentStatus, DraftStep, OutboxStatus, OutboxType
from app.domain.models import Appointment, BookingDraft, OutboxEvent


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

    # Slot-level uniqueness:
    # Prevent multiple appointments for the same start_datetime_utc.
    # If legacy DB already contains duplicates, creating the unique index
    # will fail; we keep running and rely on application-level protection.
    try:
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_appointments_slot
            ON appointments(start_datetime_utc);
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
                WHERE start_datetime_utc=?
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (start_datetime_utc,),
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

