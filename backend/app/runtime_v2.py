"""Integrated v2 runtime for Telegram ingestion, storage, queueing, and publishing."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from telegram import Bot

from app.content import ContentPipeline, PipelineDecision
from app.db import Database, SystemEvent
from app.publishing import BotPublisher, PublishQueue, PublishWorker
from app.storage import LocalMediaStore, RetentionCleaner
from app.telegram.v2_client import TelegramClientConfig, TelegramClientManager
from app.telegram.v2_listener import IngestionEvent, V2TelegramListener


class RuntimeConfigurationError(ValueError):
    """Raised when Runtime v2 cannot be constructed from the environment."""


@dataclass(frozen=True, slots=True)
class RuntimeV2Settings:
    api_id: int
    api_hash: str
    bot_token: str
    account_id: uuid.UUID
    session_path: Path
    media_root: Path
    owner_id: int | None = None
    worker_id: str = "publisher-v2"
    poll_interval_seconds: float = 1.0
    queue_batch_size: int = 10
    retry_delay_seconds: int = 60
    send_interval_seconds: float = 1.1
    retry_after_retries: int = 3
    max_retry_attempts: int = 8
    publish_concurrency: int = 4
    reconnect_delay_seconds: float = 5.0
    cleanup_interval_seconds: float = 300.0

    @classmethod
    def from_env(cls, env_file: str | Path | None = ".env") -> RuntimeV2Settings:
        if env_file is not None:
            load_dotenv(env_file, override=False)
        try:
            values = cls(
                api_id=int(_required("API_ID")),
                api_hash=_required("API_HASH"),
                bot_token=_required("BOT_TOKEN"),
                account_id=uuid.UUID(_required("V2_ACCOUNT_ID")),
                session_path=Path(
                    os.getenv("V2_SESSION_PATH", os.getenv("SESSION_PATH", "data/v2.session"))
                ),
                media_root=Path(os.getenv("V2_MEDIA_ROOT", "/app/data/media")),
                owner_id=(
                    int(os.getenv("OWNER_ID", "").strip())
                    if os.getenv("OWNER_ID", "").strip()
                    else None
                ),
                worker_id=os.getenv("V2_WORKER_ID", "publisher-v2").strip() or "publisher-v2",
                poll_interval_seconds=float(os.getenv("V2_POLL_INTERVAL_SECONDS", "1")),
                queue_batch_size=int(os.getenv("V2_QUEUE_BATCH_SIZE", "10")),
                retry_delay_seconds=int(os.getenv("V2_RETRY_DELAY_SECONDS", "60")),
                send_interval_seconds=float(os.getenv("SEND_INTERVAL_SECONDS", "1.1")),
                retry_after_retries=int(os.getenv("FLOOD_WAIT_RETRIES", "3")),
                max_retry_attempts=int(os.getenv("V2_MAX_RETRY_ATTEMPTS", "8")),
                publish_concurrency=int(os.getenv("V2_PUBLISH_CONCURRENCY", "4")),
                reconnect_delay_seconds=float(os.getenv("V2_RECONNECT_DELAY_SECONDS", "5")),
                cleanup_interval_seconds=float(os.getenv("V2_CLEANUP_INTERVAL_SECONDS", "300")),
            )
        except RuntimeConfigurationError:
            raise
        except (TypeError, ValueError) as exc:
            raise RuntimeConfigurationError("invalid Runtime v2 environment value") from exc
        values.validate()
        return values

    def validate(self) -> None:
        if self.api_id <= 0:
            raise RuntimeConfigurationError("API_ID must be positive")
        if not self.api_hash.strip():
            raise RuntimeConfigurationError("API_HASH is required")
        if not self.bot_token.strip():
            raise RuntimeConfigurationError("BOT_TOKEN is required")
        if self.owner_id is not None and self.owner_id <= 0:
            raise RuntimeConfigurationError("OWNER_ID must be positive")
        if self.poll_interval_seconds <= 0:
            raise RuntimeConfigurationError("V2_POLL_INTERVAL_SECONDS must be positive")
        if self.queue_batch_size <= 0:
            raise RuntimeConfigurationError("V2_QUEUE_BATCH_SIZE must be positive")
        if self.retry_delay_seconds <= 0:
            raise RuntimeConfigurationError("V2_RETRY_DELAY_SECONDS must be positive")
        if self.send_interval_seconds < 0:
            raise RuntimeConfigurationError("SEND_INTERVAL_SECONDS cannot be negative")
        if self.retry_after_retries < 0:
            raise RuntimeConfigurationError("FLOOD_WAIT_RETRIES cannot be negative")
        if self.max_retry_attempts <= 0:
            raise RuntimeConfigurationError("V2_MAX_RETRY_ATTEMPTS must be positive")
        if self.publish_concurrency <= 0:
            raise RuntimeConfigurationError("V2_PUBLISH_CONCURRENCY must be positive")
        if self.reconnect_delay_seconds <= 0:
            raise RuntimeConfigurationError("V2_RECONNECT_DELAY_SECONDS must be positive")
        if self.cleanup_interval_seconds <= 0:
            raise RuntimeConfigurationError("V2_CLEANUP_INTERVAL_SECONDS must be positive")


class RuntimeV2:
    """Coordinate listener, content pipeline, media storage, queue, and worker."""

    def __init__(
        self,
        *,
        database: Database,
        client_manager: Any,
        listener: Any,
        pipeline: ContentPipeline,
        media_store: LocalMediaStore,
        queue: PublishQueue,
        worker: PublishWorker,
        cleaner: RetentionCleaner,
        bot: Any,
        settings: RuntimeV2Settings,
    ) -> None:
        self.database = database
        self.client_manager = client_manager
        self.listener = listener
        self.pipeline = pipeline
        self.media_store = media_store
        self.queue = queue
        self.worker = worker
        self.cleaner = cleaner
        self.bot = bot
        self.settings = settings
        self._worker_task: asyncio.Task[None] | None = None
        self._monitor_task: asyncio.Task[None] | None = None
        self._cleanup_task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._started = False
        self._paused = False
        self._last_error: str | None = None
        self._logger = logging.getLogger(__name__)

    @classmethod
    def from_env(cls, env_file: str | Path | None = ".env") -> RuntimeV2:
        settings = RuntimeV2Settings.from_env(env_file)
        database = Database.from_env()
        client_manager = TelegramClientManager(
            TelegramClientConfig(
                api_id=settings.api_id,
                api_hash=settings.api_hash,
                session_path=settings.session_path,
            )
        )
        bot = Bot(token=settings.bot_token)
        queue = PublishQueue(
            database.session_factory,
            settings.account_id,
            worker_id=settings.worker_id,
        )
        pipeline = ContentPipeline(database.session_factory, settings.account_id)
        media_store = LocalMediaStore(
            database.session_factory,
            client_manager,
            settings.account_id,
            root=settings.media_root,
        )
        publisher = BotPublisher(
            bot,
            database.session_factory,
            settings.account_id,
            client_manager=client_manager,
            retry_after_retries=settings.retry_after_retries,
            send_interval_seconds=settings.send_interval_seconds,
            max_concurrent_sends=settings.publish_concurrency,
        )
        worker = PublishWorker(
            queue,
            publisher,
            retry_delay_seconds=settings.retry_delay_seconds,
            max_attempts=settings.max_retry_attempts,
            concurrency=settings.publish_concurrency,
            on_published=media_store.cleanup_if_ephemeral,
        )
        cleaner = RetentionCleaner(
            database.session_factory,
            settings.account_id,
        )
        listener = V2TelegramListener(
            client_manager,
            database.session_factory,
            settings.account_id,
            cls._make_ingestion_callback_placeholder(),
        )
        runtime = cls(
            database=database,
            client_manager=client_manager,
            listener=listener,
            pipeline=pipeline,
            media_store=media_store,
            queue=queue,
            worker=worker,
            cleaner=cleaner,
            bot=bot,
            settings=settings,
        )
        listener.on_message = runtime.handle_ingestion
        return runtime

    @staticmethod
    def _make_ingestion_callback_placeholder() -> Any:
        async def callback(_event: IngestionEvent) -> None:
            return None

        return callback

    @property
    def is_started(self) -> bool:
        return self._started

    @property
    def is_paused(self) -> bool:
        return self._paused

    async def start(
        self,
        *,
        check_database: bool = True,
        start_worker: bool = True,
        listener_required: bool = True,
    ) -> None:
        """Start background publishing and live ingestion.

        When listener_required=False the service stays online in degraded mode
        if Telegram authorization is missing, allowing the control surfaces to
        repair the session without a crash loop.
        """
        if self._started:
            return
        if check_database and not await self.database.ping():
            raise RuntimeError("PostgreSQL v2 health check failed")

        self._stop_event.clear()
        self._started = True
        self._paused = False
        if start_worker:
            self._ensure_background_tasks(start_monitor=False)
        try:
            await self.listener.start()
        except Exception as exc:
            self._paused = True
            self._last_error = f"{type(exc).__name__}: {exc}"
            self._logger.exception("Runtime v2 listener startup failed")
            if listener_required:
                await self._cancel_background_tasks()
                self._started = False
                raise
        else:
            self._last_error = None
            await self._send_startup_notification()

        if start_worker:
            self._ensure_background_tasks(start_monitor=True)
        self._logger.info(
            "Runtime v2 started: account_id=%s paused=%s",
            self.settings.account_id,
            self._paused,
        )

    async def pause_ingestion(self) -> None:
        if not self._started:
            self._started = True
            self._stop_event.clear()
            self._ensure_background_tasks(start_monitor=True)
        await self.listener.stop()
        self._paused = True
        self._logger.info("Runtime v2 ingestion paused")

    async def resume_ingestion(self) -> None:
        if not self._started:
            if not await self.database.ping():
                raise RuntimeError("PostgreSQL v2 health check failed")
            self._started = True
            self._stop_event.clear()
            self._ensure_background_tasks(start_monitor=True)
        try:
            await self.listener.start()
        except Exception as exc:
            self._paused = True
            self._last_error = f"{type(exc).__name__}: {exc}"
            raise
        self._paused = False
        self._last_error = None
        self._logger.info("Runtime v2 ingestion resumed")

    async def reload_ingestion(self) -> None:
        if self._paused:
            return
        await self.listener.reload()

    async def status_snapshot(self) -> dict[str, object]:
        try:
            database_ok = await self.database.ping()
        except Exception:
            database_ok = False
        worker_running = self._worker_task is not None and not self._worker_task.done()
        monitor_running = self._monitor_task is not None and not self._monitor_task.done()
        return {
            "started": self._started,
            "paused": self._paused,
            "database": database_ok,
            "telegram_connected": bool(self.client_manager.is_connected),
            "listener_running": bool(self.listener.is_running),
            "source_count": int(getattr(self.listener, "source_count", 0)),
            "worker_running": worker_running,
            "monitor_running": monitor_running,
            "cleanup_running": (
                self._cleanup_task is not None and not self._cleanup_task.done()
            ),
            "last_error": self._last_error,
        }

    async def _send_startup_notification(self) -> None:
        if self.settings.owner_id is None or not self.listener.is_running:
            return
        try:
            await self.bot.send_message(
                chat_id=self.settings.owner_id,
                text="✅ Runtime V2 يعمل الآن ويستقبل الرسائل الجديدة من المصادر النشطة.",
            )
            self._logger.info("Runtime v2 startup notification sent")
        except Exception:
            self._logger.warning("Runtime v2 startup notification failed", exc_info=True)

    async def run_forever(self) -> None:
        await self.start()
        await self._stop_event.wait()

    async def wait(self) -> None:
        await self._stop_event.wait()

    async def stop(self) -> None:
        if not self._started and self._worker_task is None and self._monitor_task is None:
            await self._close_resources()
            return
        self._stop_event.set()
        try:
            await self.listener.stop()
        finally:
            await self._cancel_background_tasks()
        self._started = False
        self._paused = True
        await self._close_resources()
        self._logger.info("Runtime v2 stopped")

    async def run_worker_once(self, *, limit: int | None = None) -> int:
        return await self.worker.run_once(limit=limit or self.settings.queue_batch_size)

    async def handle_ingestion(self, event: IngestionEvent) -> None:
        """Process one live Telegram event and enqueue accepted content."""
        result = await self.pipeline.process(event)
        stored_media = 0
        if result.decision is PipelineDecision.ACCEPTED:
            if result.content_item_id is None:
                raise RuntimeError("accepted content is missing content_item_id")
            media_store = getattr(self, "media_store", None)
            try:
                if media_store is not None:
                    stored_media = await media_store.materialize(result.content_item_id, event)
            except Exception:
                self._logger.warning(
                    "v2 media persistence failed; publisher fallback will be used",
                    exc_info=True,
                )
            await self.queue.enqueue_result(result)
        self._logger.info(
            "v2 ingestion completed: source_id=%s route_id=%s message_id=%s "
            "decision=%s reason=%s stored_media=%s",
            event.source_id,
            event.route_id,
            event.message_id,
            result.decision,
            result.reason,
            stored_media,
        )
        await self._record_event(
            "ingestion_decision",
            severity="warning" if result.decision is PipelineDecision.FILTERED else "info",
            entity_type="source_route",
            entity_id=event.route_id,
            details={
                "source_id": str(event.source_id),
                "message_id": event.message_id,
                "decision": result.decision.value,
                "reason": result.reason,
                "stored_media": stored_media,
            },
        )

    async def _record_event(
        self,
        event_type: str,
        *,
        severity: str = "info",
        entity_type: str | None = None,
        entity_id: uuid.UUID | None = None,
        error_code: str | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        database = getattr(self, "database", None)
        settings = getattr(self, "settings", None)
        if database is None or settings is None:
            return
        try:
            async with database.session_factory() as session:
                async with session.begin():
                    session.add(
                        SystemEvent(
                            account_id=settings.account_id,
                            event_type=event_type,
                            severity=severity,
                            entity_type=entity_type,
                            entity_id=entity_id,
                            error_code=error_code,
                            details=details or {},
                        )
                    )
        except Exception:
            logger = getattr(self, "_logger", None)
            debug = getattr(logger, "debug", None)
            if debug is not None:
                debug("failed to persist system event", exc_info=True)

    def _ensure_background_tasks(self, *, start_monitor: bool) -> None:
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(
                self._worker_loop(), name="v2-publish-worker"
            )
        if start_monitor and (self._monitor_task is None or self._monitor_task.done()):
            self._monitor_task = asyncio.create_task(
                self._connection_monitor(), name="v2-telegram-monitor"
            )
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(
                self._cleanup_loop(), name="v2-retention-cleaner"
            )

    async def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                processed = await self.run_worker_once()
                if processed == 0:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=self.settings.poll_interval_seconds
                    )
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            except Exception:
                self._logger.exception("v2 publish worker cycle failed")
                await asyncio.sleep(self.settings.poll_interval_seconds)

    async def _cleanup_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                result = await self.cleaner.run_once()
                if result["media_deleted"] or result["content_expired"]:
                    self._logger.info(
                        "v2 retention cleanup: media=%s content=%s",
                        result["media_deleted"],
                        result["content_expired"],
                    )
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.settings.cleanup_interval_seconds,
                )
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            except Exception:
                self._logger.exception("v2 retention cleanup cycle failed")
                await asyncio.sleep(self.settings.poll_interval_seconds)

    async def _connection_monitor(self) -> None:
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.settings.reconnect_delay_seconds,
                )
                continue
            except TimeoutError:
                pass
            if self._paused:
                continue
            if self.client_manager.is_connected and self.listener.is_running:
                continue
            try:
                await self.listener.stop()
                await self.client_manager.disconnect()
                await self.listener.start()
                self._last_error = None
                self._logger.info("v2 Telegram connection recovered")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = f"{type(exc).__name__}: {exc}"
                self._logger.warning(
                    "v2 Telegram reconnect failed: %s",
                    type(exc).__name__,
                )

    async def _cancel_background_tasks(self) -> None:
        tasks = [
            task
            for task in (self._worker_task, self._monitor_task, self._cleanup_task)
            if task is not None
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._worker_task = None
        self._monitor_task = None
        self._cleanup_task = None

    async def _close_resources(self) -> None:
        try:
            await self.client_manager.disconnect()
        finally:
            try:
                close = getattr(self.bot, "close", None)
                if close is not None:
                    await close()
            finally:
                await self.database.close()


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeConfigurationError(f"{name} is required for Runtime v2")
    return value
