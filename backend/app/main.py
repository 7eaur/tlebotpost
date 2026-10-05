"""Production entry point for the Telegram channel relay."""

from __future__ import annotations

import asyncio
import logging

from app.config import Settings
from app.control.bot import ControlBot
from app.control.resolver import TelegramChatResolver
from app.control.runtime import RelayRuntime
from app.database import Database
from app.relay.publisher import Publisher
from app.repositories.events import EventLogRepository
from app.repositories.settings import SettingsRepository
from app.repositories.sources import SourceRepository
from app.telegram.listener import SourceListener
from app.telegram.session import TelegramSession


async def run() -> None:
    settings = Settings.from_env()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger = logging.getLogger(__name__)
    database = Database(settings.database_path)
    await database.initialize()

    session = TelegramSession(
        api_id=settings.api_id,
        api_hash=settings.api_hash,
        session_path=settings.session_path,
    )
    source_repository = SourceRepository(database)
    settings_repository = SettingsRepository(database)
    event_repository = EventLogRepository(database)
    await settings_repository.update_branding(
        footer=settings.brand_footer,
        link=settings.brand_link,
    )
    await settings_repository.sync_environment_defaults(
        brand_footer=settings.brand_footer,
        brand_link=settings.brand_link,
        include_keywords=settings.include_keywords,
        exclude_keywords=settings.exclude_keywords,
    )
    await event_repository.prune(settings.event_log_keep)

    control_bot = ControlBot(
        token=settings.bot_token,
        owner_id=settings.owner_id,
        sources=source_repository,
        settings=settings_repository,
        resolver=TelegramChatResolver(session.client),
        runtime=None,
        session=session,
    )
    control_application = control_bot.build_application()

    publisher = Publisher(
        session.client,
        control_application.bot,
        settings_repository,
        event_repository,
        retry_after_retries=settings.flood_wait_retries,
        send_interval_seconds=settings.send_interval_seconds,
    )
    listener = SourceListener(
        session.client,
        source_repository,
        publisher.publish,
        album_window_seconds=settings.album_window_seconds,
    )

    async def report_status(status: str) -> None:
        logger.info("relay status: %s", status)
        if status.startswith("telegram_"):
            await control_bot.notify_owner(status)

    runtime = RelayRuntime(
        session,
        listener,
        reconnect_delays=settings.reconnect_delays,
        on_status=report_status,
    )
    control_bot.runtime = runtime

    logger.info("control bot is starting")
    try:
        await control_bot.run_polling()
    finally:
        await runtime.stop()
        await database.close()
        logger.info("relay stopped")


if __name__ == "__main__":
    asyncio.run(run())
