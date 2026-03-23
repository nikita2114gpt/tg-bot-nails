import asyncio
import os
import sqlite3
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramConflictError, TelegramNetworkError

from app.application.appointment_uc import AppointmentUseCases
from app.application.admin_ops_uc import AdminOpsUseCases
from app.application.booking_uc import BookingUseCases
from app.config import load_settings
from app.infrastructure.logging import attach_telegram_alerts, configure_logging, get_logger
from app.infrastructure.outbox_worker import OutboxWorker
from app.infrastructure.storage_filejson import (
    AppointmentRepository,
    BlacklistRepository,
    ClientLifecycleMarkerRepository,
    DayScheduleOverrideRepository,
    DraftRepository,
    OutboxRepository,
    PriceListRepository,
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
    PriceListRepository as SqlitePriceListRepository,
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

logger = get_logger(__name__)


def _resolve_storage() -> tuple[str, str]:
    base_dir = Path(__file__).resolve().parents[1]
    storage_backend = (os.getenv("BOT_STORAGE_BACKEND", "json") or "json").lower().strip()
    sqlite_db_path = os.getenv(
        "SQLITE_DB_PATH",
        str(base_dir / "data" / "bot2.sqlite3"),
    )
    return storage_backend, sqlite_db_path


def _preflight_checks() -> None:
    logger.info("startup-check: validate environment")
    raw_token = (os.getenv("BOT_TOKEN") or "").strip()
    if not raw_token:
        raise RuntimeError("startup-check failed: BOT_TOKEN is empty or missing")

    raw_admin_ids = (os.getenv("ADMIN_IDS") or "").strip()
    if not raw_admin_ids:
        raise RuntimeError("startup-check failed: ADMIN_IDS is empty or missing")

    storage_backend, sqlite_db_path = _resolve_storage()
    if storage_backend != "sqlite":
        logger.info("startup-check: sqlite probe skipped (backend=%s)", storage_backend)
        return

    db_path = Path(sqlite_db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db_path), timeout=10) as conn:
        conn.execute("SELECT 1;")
    logger.info("startup-check: sqlite is reachable (%s)", db_path)


async def _sqlite_watchdog(storage_backend: str, sqlite_db_path: str, interval_seconds: int = 60) -> None:
    if storage_backend != "sqlite":
        return

    while True:
        try:
            with sqlite3.connect(sqlite_db_path, timeout=10) as conn:
                conn.execute("SELECT 1;")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("sqlite-watchdog: database probe failed (path=%s)", sqlite_db_path)
        await asyncio.sleep(interval_seconds)


async def _run_once() -> None:
    logger.info("init: load settings")
    settings = load_settings()
    if not settings.admin_ids:
        raise RuntimeError("startup-check failed: ADMIN_IDS parsed to empty list")
    if settings.allow_multiple_active_bookings:
        logger.warning(
            "init: test mode enabled (ALLOW_MULTIPLE_ACTIVE_BOOKINGS=true)"
        )

    logger.info("init: create bot")
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    logger.info("init: create dispatcher")
    dp = Dispatcher()

    logger.info("init: create repositories")
    base_dir = Path(__file__).resolve().parents[1]
    storage_backend, sqlite_db_path = _resolve_storage()

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
            price_list_repo = SqlitePriceListRepository(db_path=sqlite_db_path)
            logger.info("init: storage backend = sqlite")

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
                logger.exception(
                    "init: json->sqlite migration failed, continue. error=%s",
                    e,
                )
        except Exception as e:
            # Фоллбэк на legacy JSON, чтобы не ломать рабочий сценарий.
            logger.exception("init: sqlite init failed, fallback to json. error=%s", e)
            draft_repo = DraftRepository()
            appointment_repo = AppointmentRepository()
            outbox_repo = OutboxRepository()
            schedule_repo = ScheduleSettingsRepository()
            day_schedule_repo = DayScheduleOverrideRepository()
            service_catalog_repo = ServiceCatalogRepository()
            blacklist_repo = BlacklistRepository()
            lifecycle_repo = ClientLifecycleMarkerRepository()
            salon_info_repo = SalonInfoSettingsRepository()
            price_list_repo = PriceListRepository()
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
        price_list_repo = PriceListRepository()

    logger.info("init: create booking use-cases")
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
        lifecycle_repo=lifecycle_repo,
        service_catalog_repo=service_catalog_repo,
    )
    admin_ops_uc = AdminOpsUseCases(
        schedule_repo=schedule_repo,
        service_repo=service_catalog_repo,
        blacklist_repo=blacklist_repo,
        day_schedule_repo=day_schedule_repo,
        salon_info_repo=salon_info_repo,
        price_list_repo=price_list_repo,
    )

    logger.info("init: create telegram sender")
    sender = TelegramSender(
        bot=bot,
        admin_ids=settings.admin_ids,
    )
    attach_telegram_alerts(bot=bot, admin_ids=settings.admin_ids)

    logger.info("init: create outbox worker")
    worker = OutboxWorker(
        outbox_repo=outbox_repo,
        sender=sender,
        interval_seconds=10,
        lifecycle_repo=lifecycle_repo,
        appointment_repo=appointment_repo,
    )

    logger.info("init: include routers")
    dp.include_router(common_router)
    dp.include_router(client_menu_router)
    dp.include_router(booking_router)
    dp.include_router(reminder_router)
    dp.include_router(admin_router)

    worker_task: asyncio.Task | None = None
    watchdog_task: asyncio.Task | None = None

    logger.info("init: bot get_me")
    try:
        me = await asyncio.wait_for(bot.get_me(), timeout=10)
    except (TelegramNetworkError, asyncio.TimeoutError, OSError):
        logger.error(
            "Ошибка: не удалось подключиться к Telegram (таймаут get_me). "
            "Проверьте интернет и доступность api.telegram.org.",
        )
        await bot.session.close()
        return

    username = getattr(me, "username", None)
    logger.info("bot started: @%s", username if username else "unknown")

    worker_task = asyncio.create_task(worker.start())
    watchdog_task = asyncio.create_task(_sqlite_watchdog(storage_backend, sqlite_db_path))

    try:
        logger.info("start polling")
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
            logger.exception(
                f"TelegramConflictError: конфликт getUpdates. "
                f"Убедись, что запущен только один инстанс бота. details={e}",
            )
            raise
        except KeyboardInterrupt:
            raise
        except Exception:
            logger.exception("polling crashed with unhandled exception")
            raise
    finally:
        logger.info("shutdown: stopping worker")
        worker.stop()
        if worker_task is not None and not worker_task.done():
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass

        if watchdog_task is not None and not watchdog_task.done():
            watchdog_task.cancel()
            try:
                await watchdog_task
            except asyncio.CancelledError:
                pass

        logger.info("shutdown: closing bot session")
        await bot.session.close()


if __name__ == "__main__":
    configure_logging()
    try:
        _preflight_checks()
        asyncio.run(_run_once())
    except KeyboardInterrupt:
        logger.info("shutdown: interrupted by user")
    except Exception:
        logger.exception("fatal: bot process terminated by unhandled exception")
        raise SystemExit(1)