import asyncio
import os
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramConflictError, TelegramNetworkError

from app.application.appointment_uc import AppointmentUseCases
from app.application.admin_ops_uc import AdminOpsUseCases
from app.application.booking_uc import BookingUseCases
from app.config import load_settings
from app.infrastructure.outbox_worker import OutboxWorker
from app.infrastructure.storage_filejson import (
    AppointmentRepository,
    BlacklistRepository,
    ClientLifecycleMarkerRepository,
    DayScheduleOverrideRepository,
    DraftRepository,
    OutboxRepository,
    SalonInfoSettingsRepository,
    ScheduleSettingsRepository,
    ServiceCatalogRepository,
)
from app.infrastructure.storage_sqlite import (
    AppointmentRepository as SqliteAppointmentRepository,
    BlacklistRepository as SqliteBlacklistRepository,
    ClientLifecycleMarkerRepository as SqliteClientLifecycleMarkerRepository,
    DayScheduleOverrideRepository as SqliteDayScheduleOverrideRepository,
    DraftRepository as SqliteDraftRepository,
    OutboxRepository as SqliteOutboxRepository,
    SalonInfoSettingsRepository as SqliteSalonInfoSettingsRepository,
    ScheduleSettingsRepository as SqliteScheduleSettingsRepository,
    ServiceCatalogRepository as SqliteServiceCatalogRepository,
)
from app.infrastructure.telegram_sender import TelegramSender
from app.presentation.handlers.admin_handlers import router as admin_router
from app.presentation.handlers.booking_handlers import router as booking_router
from app.presentation.handlers.client_menu_handlers import router as client_menu_router
from app.presentation.handlers.common_handlers import router as common_router
from app.presentation.handlers.reminder_handlers import router as reminder_router


async def main():
    print("init: load settings", flush=True)
    settings = load_settings()
    if settings.allow_multiple_active_bookings:
        print(
            "init: test mode — ALLOW_MULTIPLE_ACTIVE_BOOKINGS: несколько активных записей на пользователя",
            flush=True,
        )

    print("init: create bot", flush=True)
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    print("init: create dispatcher", flush=True)
    dp = Dispatcher()

    print("init: create repositories", flush=True)
    base_dir = Path(__file__).resolve().parents[1]
    storage_backend = (os.getenv("BOT_STORAGE_BACKEND", "json") or "json").lower().strip()
    sqlite_db_path = os.getenv(
        "SQLITE_DB_PATH",
        str(base_dir / "data" / "bot2.sqlite3"),
    )

    if storage_backend == "sqlite":
        try:
            draft_repo = SqliteDraftRepository(db_path=sqlite_db_path)
            appointment_repo = SqliteAppointmentRepository(db_path=sqlite_db_path)
            outbox_repo = SqliteOutboxRepository(db_path=sqlite_db_path)
            schedule_repo = SqliteScheduleSettingsRepository(db_path=sqlite_db_path)
            day_schedule_repo = SqliteDayScheduleOverrideRepository(db_path=sqlite_db_path)
            service_catalog_repo = SqliteServiceCatalogRepository(db_path=sqlite_db_path)
            blacklist_repo = SqliteBlacklistRepository(db_path=sqlite_db_path)
            lifecycle_repo = SqliteClientLifecycleMarkerRepository(db_path=sqlite_db_path)
            salon_info_repo = SqliteSalonInfoSettingsRepository(db_path=sqlite_db_path)
            print("init: storage backend = sqlite", flush=True)

            # One-time migration for legacy JSON storage.
            try:
                from app.infrastructure.migrations.json_to_sqlite import (
                    migrate_json_to_sqlite,
                )

                migrate_json_to_sqlite(
                    json_path=base_dir / "data" / "storage.json",
                    db_path=sqlite_db_path,
                )
            except Exception as e:
                # Не падаем, если JSON битый/отсутствует.
                print(
                    f"init: json->sqlite migration failed, continue. error={e}",
                    flush=True,
                )
        except Exception as e:
            # Фоллбэк на legacy JSON, чтобы не ломать рабочий сценарий.
            print(f"init: sqlite init failed, fallback to json. error={e}", flush=True)
            draft_repo = DraftRepository()
            appointment_repo = AppointmentRepository()
            outbox_repo = OutboxRepository()
            schedule_repo = ScheduleSettingsRepository()
            day_schedule_repo = DayScheduleOverrideRepository()
            service_catalog_repo = ServiceCatalogRepository()
            blacklist_repo = BlacklistRepository()
            lifecycle_repo = ClientLifecycleMarkerRepository()
            salon_info_repo = SalonInfoSettingsRepository()
    else:
        draft_repo = DraftRepository()
        appointment_repo = AppointmentRepository()
        outbox_repo = OutboxRepository()
        schedule_repo = ScheduleSettingsRepository()
        day_schedule_repo = DayScheduleOverrideRepository()
        service_catalog_repo = ServiceCatalogRepository()
        blacklist_repo = BlacklistRepository()
        lifecycle_repo = ClientLifecycleMarkerRepository()
        salon_info_repo = SalonInfoSettingsRepository()

    print("init: create booking use-cases", flush=True)
    booking_uc = BookingUseCases(
        draft_repo=draft_repo,
        appointment_repo=appointment_repo,
        outbox_repo=outbox_repo,
        allowed_services=settings.services,
        service_catalog_repo=service_catalog_repo,
        blacklist_repo=blacklist_repo,
        lifecycle_repo=lifecycle_repo,
        allow_multiple_active_bookings=settings.allow_multiple_active_bookings,
    )

    appointment_uc = AppointmentUseCases(
        appointment_repo=appointment_repo,
        allowed_services=settings.services,
        outbox_repo=outbox_repo,
    )
    admin_ops_uc = AdminOpsUseCases(
        schedule_repo=schedule_repo,
        service_repo=service_catalog_repo,
        blacklist_repo=blacklist_repo,
        day_schedule_repo=day_schedule_repo,
        salon_info_repo=salon_info_repo,
    )

    print("init: create telegram sender", flush=True)
    sender = TelegramSender(
        bot=bot,
        admin_ids=settings.admin_ids,
    )

    print("init: create outbox worker", flush=True)
    worker = OutboxWorker(
        outbox_repo=outbox_repo,
        sender=sender,
        interval_seconds=10,
        lifecycle_repo=lifecycle_repo,
        appointment_repo=appointment_repo,
    )

    print("init: include routers", flush=True)
    dp.include_router(common_router)
    dp.include_router(client_menu_router)
    dp.include_router(booking_router)
    dp.include_router(reminder_router)
    dp.include_router(admin_router)

    worker_task: asyncio.Task | None = None

    print("init: bot get_me", flush=True)
    try:
        me = await asyncio.wait_for(bot.get_me(), timeout=10)
    except (TelegramNetworkError, asyncio.TimeoutError, OSError):
        print(
            "Ошибка: не удалось подключиться к Telegram (таймаут get_me). "
            "Проверьте интернет и доступность api.telegram.org.",
            flush=True,
        )
        await bot.session.close()
        return

    username = getattr(me, "username", None)
    print(f"bot started: @{username}" if username else "bot started (no username)", flush=True)

    worker_task = asyncio.create_task(worker.start())

    try:
        print("init: start polling", flush=True)
        try:
            await dp.start_polling(
                bot,
                booking_uc=booking_uc,
                appointment_uc=appointment_uc,
                admin_ops_uc=admin_ops_uc,
                settings=settings,
                appointment_repo=appointment_repo,
                outbox_repo=outbox_repo,
                lifecycle_repo=lifecycle_repo,
                close_bot_session=False,
            )
        except TelegramConflictError as e:
            # Типовая ситуация, когда запущено более одного экземпляра бота
            # (aiogram при getUpdates/long polling получает Conflict).
            print(
                f"TelegramConflictError: конфликт getUpdates. "
                f"Убедись, что запущен только один инстанс бота. details={e}",
                flush=True,
            )
            return
        except KeyboardInterrupt:
            print("KeyboardInterrupt: shutdown requested", flush=True)
            return
    finally:
        print("shutdown: stopping worker", flush=True)
        worker.stop()
        if worker_task is not None and not worker_task.done():
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass

        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())