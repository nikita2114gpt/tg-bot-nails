import asyncio
import sqlite3
from datetime import datetime

from app.domain.enums import OutboxType
from app.domain.models import OutboxEvent


class OutboxWorker:
    def __init__(self, outbox_repo, sender, interval_seconds: int = 5):
        self.outbox_repo = outbox_repo
        self.sender = sender
        self.interval_seconds = interval_seconds
        self._running = False

    async def start(self):
        self._running = True

        while self._running:
            await self._process_once()
            await asyncio.sleep(self.interval_seconds)

    def stop(self):
        self._running = False

    async def _process_once(self):
        now_iso = datetime.utcnow().isoformat()
        events = self.outbox_repo.list_pending()

        for event in events:
            if event.send_at and event.send_at > now_iso:
                continue

            try:
                if event.event_type == OutboxType.ADMIN_NOTIFY:
                    await self.sender.send_admin_notify(event)
                elif event.event_type == OutboxType.REMINDER_CLIENT:
                    await self.sender.send_client_reminder(event)
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