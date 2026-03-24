from __future__ import annotations

from datetime import datetime

from aiogram import F, Router
from aiogram.types import CallbackQuery

from app.application.appointment_uc import AppointmentUseCases
from app.core.errors import AppError, error_to_user_message
from app.domain.enums import AppointmentStatus, OutboxType
from app.domain.models import OutboxEvent
from app.domain.ops_models import ClientLifecycleMarker
from app.infrastructure.safe_telegram import safe_answer_callback
from app.presentation.callback.reminder_callbacks import parse_reminder_callback

router = Router(name="reminder_callbacks")


async def _safe_answer(callback: CallbackQuery, text: str) -> None:
    await safe_answer_callback(callback, text, show_alert=False, cache_time=0)


@router.callback_query(F.data.startswith("r1|"))
async def reminder_callback_router(
    callback: CallbackQuery,
    appointment_uc: AppointmentUseCases,
    lifecycle_repo,
    outbox_repo,
) -> None:
    if callback.from_user is None:
        return
    uid = callback.from_user.id
    raw = callback.data or ""
    try:
        action, appointment_id = parse_reminder_callback(raw)
    except ValueError:
        await _safe_answer(callback, "Кнопка устарела")
        return

    ap = appointment_uc.appointment_repo.get_by_id(appointment_id)
    if ap is None or ap.user_id != uid:
        await _safe_answer(callback, "Запись не найдена")
        return

    if action == "ok":
        if ap.status != AppointmentStatus.CONFIRMED:
            await _safe_answer(callback, "Эта запись уже неактуальна")
            return
        now_iso = datetime.utcnow().isoformat()
        marker = lifecycle_repo.get_by_user_id(uid)
        if marker is None:
            marker = ClientLifecycleMarker(user_id=uid)
        marker.last_confirmed_at = now_iso
        marker.last_confirmed_appointment_id = ap.appointment_id
        marker.updated_at = now_iso
        lifecycle_repo.save(marker)

        key = f"admin_event:client_confirmed:{ap.appointment_id}"
        if outbox_repo.get_by_idempotency_key(key) is None:
            outbox_repo.save_event(
                OutboxEvent(
                    event_type=OutboxType.ADMIN_NOTIFY,
                    idempotency_key=key,
                    payload={
                        "event_kind": "client_confirmed",
                        "appointment_id": ap.appointment_id,
                        "draft_id": ap.draft_id,
                        "user_id": ap.user_id,
                        "service_id": ap.service_id,
                        "start_datetime_utc": ap.start_datetime_utc,
                        "customer_name": ap.customer_name,
                        "phone_e164": ap.phone_e164,
                    },
                )
            )
        await _safe_answer(callback, "Спасибо, запись подтверждена")
        return

    if action == "cx":
        try:
            _, already = appointment_uc.cancel_my_appointment_by_id(uid, appointment_id)
        except AppError as e:
            await _safe_answer(callback, error_to_user_message(e))
            return
        await _safe_answer(
            callback,
            "Запись уже была отменена" if already else "Запись отменена",
        )
        return

    await _safe_answer(callback, "Кнопка устарела")
