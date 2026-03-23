import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from app.domain.enums import AppointmentStatus, DraftStep, OutboxStatus, OutboxType
from app.domain.models import Appointment, BookingDraft, OutboxEvent
from app.infrastructure.logging import get_logger
from app.infrastructure.storage_sqlite import (
    AppointmentRepository,
    DraftRepository,
    OutboxRepository,
)

logger = get_logger(__name__)


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


def _parse_enum(enum_cls: type[Any], raw: Any, default: Any) -> Any:
    try:
        if isinstance(raw, enum_cls):
            return raw
        return enum_cls(raw)
    except Exception:
        return default


def _safe_load_json(storage_json_path: Path) -> tuple[dict[str, Any], bool]:
    if not storage_json_path.exists():
        return {"drafts": [], "appointments": [], "outbox": []}, True

    try:
        with storage_json_path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except json.JSONDecodeError:
        logger.warning(
            f"init: json->sqlite migration: JSONDecodeError for {storage_json_path}. "
            f"Backing up and continuing with empty storage.",
        )
        backup_name = (
            f"{storage_json_path.name}.bak.{datetime.utcnow().strftime('%Y%m%dT%H%M%S')}"
        )
        try:
            storage_json_path.replace(storage_json_path.with_name(backup_name))
        except Exception:
            pass
        return {"drafts": [], "appointments": [], "outbox": []}, False
    except Exception:
        return {"drafts": [], "appointments": [], "outbox": []}, False

    if not isinstance(raw, dict):
        return {"drafts": [], "appointments": [], "outbox": []}, False

    return (
        {
        "drafts": raw.get("drafts") if isinstance(raw.get("drafts"), list) else [],
        "appointments": raw.get("appointments")
        if isinstance(raw.get("appointments"), list)
        else [],
        "outbox": raw.get("outbox") if isinstance(raw.get("outbox"), list) else [],
        },
        True,
    )


def _ensure_meta_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """
    )


def _get_meta_value(conn: sqlite3.Connection, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM meta WHERE key=? LIMIT 1", (key,)).fetchone()
    return row[0] if row is not None else None


def _set_meta_value(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO meta(key, value)
        VALUES(?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """,
        (key, value),
    )


def migrate_json_to_sqlite(json_path: str | Path, db_path: str | Path) -> None:
    """
    Одноразовая идемпотентная миграция data/storage.json -> SQLite.

    Критерий повторного запуска:
    - маркер в SQLite meta таблице (чтобы не перезаписывать runtime outbox статусы).
    """

    json_path = Path(json_path)
    db_path = Path(db_path)

    meta_key = "json_to_sqlite_done_v1"

    db_path.parent.mkdir(parents=True, exist_ok=True)

    # 1) Проверяем маркер.
    with sqlite3.connect(str(db_path), timeout=10) as conn:
        _ensure_meta_table(conn)
        done = _get_meta_value(conn, meta_key) is not None
        if done:
            return

    # 2) Загружаем JSON (без падений).
    data, json_ok = _safe_load_json(json_path)

    # 3) Если JSON пустой — все равно ставим маркер, чтобы миграция не повторялась.
    draft_items = data.get("drafts", [])
    appointment_items = data.get("appointments", [])
    outbox_items = data.get("outbox", [])

    had_errors = not json_ok

    draft_repo = DraftRepository(db_path=db_path)
    appointment_repo = AppointmentRepository(db_path=db_path)
    outbox_repo = OutboxRepository(db_path=db_path)

    # 4) Мигрируем drafts.
    for d in draft_items:
        if not isinstance(d, dict):
            continue
        draft_id = d.get("draft_id")
        user_id_raw = d.get("user_id")
        if not draft_id or not isinstance(draft_id, str):
            continue
        try:
            user_id = int(user_id_raw)
        except Exception:
            continue

        step = _parse_enum(DraftStep, d.get("step"), DraftStep.CANCELLED)

        created_at = d.get("created_at") if isinstance(d.get("created_at"), str) else _now_iso()
        updated_at = d.get("updated_at") if isinstance(d.get("updated_at"), str) else _now_iso()

        draft = BookingDraft(
            draft_id=draft_id,
            user_id=user_id,
            step=step,
            service_id=d.get("service_id") if isinstance(d.get("service_id"), str) else None,
            appointment_date=d.get("appointment_date")
            if isinstance(d.get("appointment_date"), str)
            else None,
            appointment_time=d.get("appointment_time")
            if isinstance(d.get("appointment_time"), str)
            else None,
            start_datetime_utc=d.get("start_datetime_utc")
            if isinstance(d.get("start_datetime_utc"), str)
            else None,
            customer_name=d.get("customer_name")
            if isinstance(d.get("customer_name"), str)
            else None,
            phone_e164=d.get("phone_e164") if isinstance(d.get("phone_e164"), str) else None,
            created_at=created_at,
            updated_at=updated_at,
        )
        try:
            draft_repo.save_draft(draft)
        except sqlite3.IntegrityError as e:
            had_errors = True
            logger.warning(
                f"init: json->sqlite migration: failed to save draft_id={draft_id}. "
                f"error={e}",
            )
        except Exception as e:
            had_errors = True
            logger.exception(
                f"init: json->sqlite migration: unexpected error saving draft_id={draft_id}. "
                f"error={e}",
            )

    # 5) Мигрируем appointments.
    for a in appointment_items:
        if not isinstance(a, dict):
            continue
        appointment_id = a.get("appointment_id")
        draft_id = a.get("draft_id")
        user_id_raw = a.get("user_id")
        if not appointment_id or not isinstance(appointment_id, str):
            continue
        if not draft_id or not isinstance(draft_id, str):
            continue
        try:
            user_id = int(user_id_raw)
        except Exception:
            continue

        status = _parse_enum(
            AppointmentStatus, a.get("status"), AppointmentStatus.CANCELLED
        )

        created_at = a.get("created_at") if isinstance(a.get("created_at"), str) else _now_iso()
        updated_at = a.get("updated_at") if isinstance(a.get("updated_at"), str) else _now_iso()

        ap = Appointment(
            appointment_id=appointment_id,
            draft_id=draft_id,
            user_id=user_id,
            service_id=a.get("service_id") if isinstance(a.get("service_id"), str) else "",
            start_datetime_utc=a.get("start_datetime_utc")
            if isinstance(a.get("start_datetime_utc"), str)
            else "",
            customer_name=a.get("customer_name")
            if isinstance(a.get("customer_name"), str)
            else "",
            phone_e164=a.get("phone_e164") if isinstance(a.get("phone_e164"), str) else "",
            status=status,
            created_at=created_at,
            updated_at=updated_at,
        )
        try:
            appointment_repo.save_appointment(ap)
        except sqlite3.IntegrityError as e:
            had_errors = True
            logger.warning(
                f"init: json->sqlite migration: failed to save appointment_id={appointment_id}. "
                f"error={e}",
            )
        except Exception as e:
            had_errors = True
            logger.exception(
                f"init: json->sqlite migration: unexpected error saving appointment_id={appointment_id}. "
                f"error={e}",
            )

    # 6) Мигрируем outbox.
    for e in outbox_items:
        if not isinstance(e, dict):
            continue
        event_id = e.get("event_id")
        idempotency_key = e.get("idempotency_key")
        if not event_id or not isinstance(event_id, str):
            continue
        if not idempotency_key or not isinstance(idempotency_key, str):
            continue

        # Keep legacy semantics from storage_filejson._outbox_from_dict:
        # - invalid event_type => force status=FAILED
        event_type_raw = e.get("event_type")
        type_valid = True
        try:
            event_type = OutboxType(event_type_raw)
        except Exception:
            type_valid = False
            event_type = OutboxType.ADMIN_NOTIFY

        status_raw = e.get("status")
        try:
            status = OutboxStatus(status_raw)
        except Exception:
            status = OutboxStatus.FAILED

        if not type_valid:
            status = OutboxStatus.FAILED

        payload = e.get("payload", {})
        if not isinstance(payload, dict):
            payload = {}

        send_at = e.get("send_at") if isinstance(e.get("send_at"), str) else None

        attempts_raw = e.get("attempts", 0)
        attempts = attempts_raw if isinstance(attempts_raw, int) else 0

        last_error = e.get("last_error") if isinstance(e.get("last_error"), str) else None

        created_at = e.get("created_at") if isinstance(e.get("created_at"), str) else _now_iso()
        updated_at = e.get("updated_at") if isinstance(e.get("updated_at"), str) else _now_iso()

        ev = OutboxEvent(
            event_id=event_id,
            event_type=event_type,
            status=status,
            idempotency_key=idempotency_key,
            send_at=send_at,
            payload=payload,
            attempts=attempts,
            last_error=last_error,
            created_at=created_at,
            updated_at=updated_at,
        )
        try:
            outbox_repo.save_event(ev)
        except sqlite3.IntegrityError as e:
            had_errors = True
            logger.warning(
                f"init: json->sqlite migration: failed to save outbox event_id={event_id} key={idempotency_key}. "
                f"error={e}",
            )
        except Exception as e:
            had_errors = True
            logger.exception(
                f"init: json->sqlite migration: unexpected error saving outbox event_id={event_id} key={idempotency_key}. "
                f"error={e}",
            )

    # 6.5) Прайс (отдельная таблица; читаем полный JSON).
    try:
        if json_path.exists():
            with json_path.open("r", encoding="utf-8") as f:
                full_raw = json.load(f)
        else:
            full_raw = {}
    except Exception:
        full_raw = {}
    pl_items = (
        full_raw.get("price_list")
        if isinstance(full_raw, dict) and isinstance(full_raw.get("price_list"), list)
        else []
    )
    from app.domain.ops_models import PriceListItem
    from app.infrastructure.storage_sqlite import PriceListRepository

    price_repo = PriceListRepository(db_path=db_path)
    for row in pl_items:
        if not isinstance(row, dict):
            continue
        item_id = row.get("item_id")
        if not isinstance(item_id, str) or not item_id:
            continue
        item = PriceListItem(
            item_id=item_id,
            display_text=str(row.get("display_text") or ""),
            is_active=bool(row.get("is_active", True)),
            created_at=row.get("created_at") if isinstance(row.get("created_at"), str) else _now_iso(),
            updated_at=row.get("updated_at") if isinstance(row.get("updated_at"), str) else _now_iso(),
        )
        try:
            price_repo.save(item)
        except sqlite3.IntegrityError as e:
            had_errors = True
            logger.warning(
                f"init: json->sqlite migration: failed price_list item_id={item_id}. error={e}",
            )
        except Exception as e:
            had_errors = True
            logger.exception(
                f"init: json->sqlite migration: unexpected error price_list item_id={item_id}. error={e}",
            )

    # 7) Ставим маркер только если не было ошибок сохранения.
    if had_errors:
        logger.warning(
            "init: json->sqlite migration: finished with errors; marker not set to allow retry.",
        )
        return

    with sqlite3.connect(str(db_path), timeout=10) as conn:
        _ensure_meta_table(conn)
        _set_meta_value(conn, meta_key, _now_iso())

