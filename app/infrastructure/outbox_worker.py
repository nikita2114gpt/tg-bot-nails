import asyncio
import sqlite3
from datetime import datetime, timedelta

from app.domain.enums import AppointmentStatus, OutboxType
from app.domain.models import OutboxEvent
from app.domain.ops_models import ClientLifecycleMarker


class OutboxWorker:
    def __init__(
        self,
        outbox_repo,
        sender,
        interval_seconds: int = 5,
        lifecycle_repo=None,
        appointment_repo=None,
    ):
        self.outbox_repo = outbox_repo
        self.sender = sender
        self.interval_seconds = interval_seconds
        self.lifecycle_repo = lifecycle_repo
        self.appointment_repo = appointment_repo
        self._running = False

    async def start(self):
        self._running = True

        while self._running:
            await self._process_once()
            await asyncio.sleep(self.interval_seconds)

    def stop(self):
        self._running = False

    def _delivery_allowed_for_reminder_client(self, event: OutboxEvent) -> bool:
        """
        Pending REMINDER_CLIENT создаётся при confirm и не удаляется при cancel записи.
        Перед отправкой сверяемся с БД: только CONFIRMED и существующий appointment_id.
        """
        if self.appointment_repo is None:
            return True
        p = event.payload if isinstance(event.payload, dict) else {}
        appointment_id = str(p.get("appointment_id") or "").strip()
        if not appointment_id:
            return True
        try:
            ap = self.appointment_repo.get_by_id(appointment_id)
        except Exception:
            return False
        if ap is None:
            return False
        return ap.status == AppointmentStatus.CONFIRMED

    async def _process_once(self):
        now_iso = datetime.utcnow().isoformat()
        self._enqueue_reactivation_events(now_iso)
        events = self.outbox_repo.list_pending()

        for event in events:
            if event.send_at and event.send_at > now_iso:
                continue

            try:
                if event.event_type == OutboxType.ADMIN_NOTIFY:
                    await self.sender.send_admin_notify(event)
                elif event.event_type == OutboxType.REMINDER_CLIENT:
                    if not self._delivery_allowed_for_reminder_client(event):
                        self.outbox_repo.mark_failed(
                            event.event_id,
                            "skipped: appointment missing or not confirmed",
                        )
                        continue
                    await self.sender.send_client_reminder(event)
                    self._handle_no_confirm_alert(event)
                else:
                    self.outbox_repo.mark_failed(
                        event.event_id,
                        f"Unknown event_type={event.event_type}",
                    )
                    continue

                try:
                    self.outbox_repo.mark_sent(event.event_id)
                except sqlite3.OperationalError as e:
                    # SQLite can be temporarily busy/locked.
                    # Retry once to avoid losing notifications by switching to FAILED.
                    if "locked" in str(e).lower() or "busy" in str(e).lower():
                        await asyncio.sleep(0.1)
                        self.outbox_repo.mark_sent(event.event_id)
                    else:
                        raise

            except Exception as e:
                self.outbox_repo.mark_failed(event.event_id, str(e))

    def _handle_no_confirm_alert(self, event: OutboxEvent) -> None:
        p = event.payload
        try:
            hours_before = int(p.get("hours_before"))
        except Exception:
            return
        if hours_before != 2:
            return
        if self.lifecycle_repo is None:
            return

        appointment_id = str(p.get("appointment_id") or "")
        if not appointment_id:
            return
        user_raw = p.get("user_id")
        try:
            user_id = int(user_raw)
        except Exception:
            return

        marker = self.lifecycle_repo.get_by_user_id(user_id)
        if marker is not None and marker.last_confirmed_appointment_id == appointment_id:
            return
        if marker is not None and marker.last_no_confirm_alert_appointment_id == appointment_id:
            return

        key = f"admin_event:client_not_confirmed:{appointment_id}"
        if self.outbox_repo.get_by_idempotency_key(key) is None:
            self.outbox_repo.save_event(
                OutboxEvent(
                    event_type=OutboxType.ADMIN_NOTIFY,
                    idempotency_key=key,
                    payload={
                        "event_kind": "client_not_confirmed",
                        "appointment_id": appointment_id,
                        "draft_id": p.get("draft_id"),
                        "user_id": user_id,
                        "service_id": p.get("service_id"),
                        "start_datetime_utc": p.get("start_datetime_utc"),
                        "customer_name": p.get("customer_name"),
                        "phone_e164": p.get("phone_e164"),
                    },
                )
            )

        if marker is None:
            marker = ClientLifecycleMarker(user_id=user_id)
        marker.last_no_confirm_alert_appointment_id = appointment_id
        marker.updated_at = datetime.utcnow().isoformat()
        self.lifecycle_repo.save(marker)

    def _enqueue_reactivation_events(self, now_iso: str) -> None:
        if self.appointment_repo is None or self.lifecycle_repo is None:
            return

        now = datetime.utcnow()
        now_key = now.strftime("%Y%m%dT%H%M")
        threshold = now - timedelta(days=30)

        try:
            appointments = self.appointment_repo.list_all()
        except Exception:
            return

        by_user: dict[int, list] = {}
        for ap in appointments:
            if getattr(ap, "status", None) is None:
                continue
            if str(ap.status.value) != "confirmed":
                continue
            try:
                ap_uid = int(ap.user_id)
            except (TypeError, ValueError):
                continue
            if ap_uid == 0:
                continue
            by_user.setdefault(ap_uid, []).append(ap)

        for user_id, rows in by_user.items():
            # Safety: never reactivate users with active future booking.
            active = None
            fn = getattr(self.appointment_repo, "get_active_confirmed_for_user", None)
            if callable(fn):
                try:
                    active = fn(user_id)
                except Exception:
                    active = None
            if active is not None:
                continue

            past = [x for x in rows if (x.start_datetime_utc or "") <= now_key]
            if not past:
                continue
            past.sort(key=lambda x: (x.start_datetime_utc or "", x.appointment_id or ""))
            last = past[-1]

            # Source-of-truth date for 30-day rule: appointment slot datetime.
            try:
                last_dt = datetime.strptime(last.start_datetime_utc, "%Y%m%dT%H%M")
            except Exception:
                continue
            if last_dt > threshold:
                continue

            marker = self.lifecycle_repo.get_by_user_id(user_id)
            if marker is not None and isinstance(marker.last_reactivation_sent_at, str):
                try:
                    sent_dt = datetime.fromisoformat(marker.last_reactivation_sent_at)
                    if sent_dt > threshold:
                        continue
                except Exception:
                    pass

            key = f"reactivation:{user_id}:{last.appointment_id}"
            if self.outbox_repo.get_by_idempotency_key(key) is None:
                self.outbox_repo.save_event(
                    OutboxEvent(
                        event_type=OutboxType.REMINDER_CLIENT,
                        idempotency_key=key,
                        payload={
                            "reminder_kind": "reactivation",
                            "user_id": user_id,
                            "service_id": last.service_id,
                            "start_datetime_utc": last.start_datetime_utc,
                            "appointment_id": last.appointment_id,
                            "customer_name": last.customer_name,
                        },
                    )
                )

            if marker is None:
                marker = ClientLifecycleMarker(user_id=user_id)
            marker.last_reactivation_sent_at = now_iso
            marker.updated_at = now_iso
            self.lifecycle_repo.save(marker)