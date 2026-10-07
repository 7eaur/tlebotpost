"""Integrated v2 runtime for Telegram ingestion and publishing."""

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
from app.db import Database
from app.publishing import BotPublisher, PublishQueue, PublishWorker
from app.telegram.v2_client import TelegramClientConfig, TelegramClientManager
from app.telegram.v2_listener import IngestionEvent, V2TelegramListener


class RuntimeConfigurationError(ValueError):
    """Raised when Runtime v2 cannot be constructed from the environment."""


@dataclass(frozen=True, slots=True)
class RuntimeV2Settings:
    """Runtime-only settings; database settings remain owned by Database.from_env()."""

    api_id: int
    api_hash: str
    bot_token: str
    account_id: uuid.UUID
    session_path: Path
    worker_id: str = "publisher-v2"
    poll_interval_seconds: float = 1.0
    queue_batch_size: int = 10
    retry_delay_seconds: int = 60
    send_interval_seconds: float = 1.1
    retry_after_retries: int = 3

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
                worker_id=os.getenv("V2_WORKER_ID", "publisher-v2").strip() or "publisher-v2",
                poll_interval_seconds=float(os.getenv("V2_POLL_INTERVAL_SECONDS", "1")),
                queue_batch_size=int(os.getenv("V2_QUEUE_BATCH_SIZE", "10")),
                retry_delay_seconds=int(os.getenv("V2_RETRY_DELAY_SECONDS", "60")),
                send_interval_seconds=float(os.getenv("SEND_INTERVAL_SECONDS", "1.1")),
                retry_after_retries=int(os.getenv("FLOOD_WAIT_RETRIES", "3")),
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


class RuntimeV2:
    """Coordinate listener, content pipeline, publish queue, and worker."""

    def __init__(
        self,
        *,
        database: Database,
        client_manager: Any,
        listener: Any,
        pipeline: ContentPipeline,
        queue: PublishQueue,
        worker: PublishWorker,
        bot: Any,
        settings: RuntimeV2Settings,
    ) -> None:
        self.database = database
        self.client_manager = client_manager
        self.listener = listener
        self.pipeline = pipeline
        self.queue = queue
        self.worker = worker
        self.bot = bot
        self.settings = settings
        self._worker_task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._started = False
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
        worker = PublishWorker(
            queue,
            BotPublisher(
                bot,
                database.session_factory,
                settings.account_id,
                retry_after_retries=settings.retry_after_retries,
                send_interval_seconds=settings.send_interval_seconds,
            ),
            retry_delay_seconds=settings.retry_delay_seconds,
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
            queue=queue,
            worker=worker,
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

    async def start(self, *, check_database: bool = True, start_worker: bool = True) -> None:
        """Validate dependencies, start listening, then start the queue worker."""
        if self._started:
            return
        if check_database and not await self.database.ping():
            raise RuntimeError("PostgreSQL v2 health check failed")
        await self.listener.start()
        self._stop_event.clear()
        self._started = True
        if start_worker:
            self._worker_task = asyncio.create_task(self._worker_loop(), name="v2-publish-worker")
        self._logger.info("Runtime v2 started: account_id=%s", self.settings.account_id)

    async def run_forever(self) -> None:
        await self.start()
        await self._stop_event.wait()

    async def wait(self) -> None:
        """Wait until stop is requested after the runtime has started."""
        await self._stop_event.wait()

    async def stop(self) -> None:
        """Stop new events first, drain worker task, then close external clients."""
        if not self._started and self._worker_task is None:
            await self._close_resources()
            return
        self._stop_event.set()
        await self.listener.stop()
        if self._worker_task is not None:
            self._worker_task.cancel()
            await asyncio.gather(self._worker_task, return_exceptions=True)
            self._worker_task = None
        self._started = False
        await self._close_resources()
        self._logger.info("Runtime v2 stopped")

    async def run_worker_once(self, *, limit: int | None = None) -> int:
        """Process one bounded batch; useful for smoke tests and cron execution."""
        return await self.worker.run_once(limit=limit or self.settings.queue_batch_size)

    async def handle_ingestion(self, event: IngestionEvent) -> None:
        """Send one live Telegram event through content processing and enqueue it."""
        result = await self.pipeline.process(event)
        if result.decision is PipelineDecision.ACCEPTED:
            await self.queue.enqueue_result(result)
        self._logger.info(
            "v2 ingestion completed: source_id=%s route_id=%s message_id=%s decision=%s reason=%s",
            event.source_id,
            event.route_id,
            event.message_id,
            result.decision,
            result.reason,
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
