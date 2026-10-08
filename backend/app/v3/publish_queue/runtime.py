"""Startup recovery bridge for V3 dedup and publish-queue handoffs."""

from __future__ import annotations

import asyncio
import logging

from app.v3.content import ContentProcessingCoordinator
from app.v3.deduplication import DeduplicationCoordinator

from .service import PublishQueueV3


class QueueReliabilityRecoveryComponent:
    """Recover durable pre-queue and stale-lease work before Telegram ingestion starts."""

    name = "publish-queue-recovery"

    def __init__(
        self,
        *,
        processing: ContentProcessingCoordinator,
        deduplication: DeduplicationCoordinator,
        queue: PublishQueueV3,
        batch_size: int = 100,
        interval_seconds: float = 5.0,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        self.processing = processing
        self.deduplication = deduplication
        self.queue = queue
        self.batch_size = batch_size
        self.interval_seconds = interval_seconds
        self._logger = logging.getLogger(__name__)
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop_event.clear()
        await self._recover_once()
        self._task = asyncio.create_task(
            self._recovery_loop(),
            name="v3-publish-queue-recovery",
        )

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _recover_once(self) -> None:
        stale = await self.queue.recover_expired_leases(limit=self.batch_size)
        processing = await self.processing.recover_pending(limit=self.batch_size)
        dedup = await self.deduplication.recover_pending(limit=self.batch_size)
        queued = await self.queue.recover_pending(limit=self.batch_size)
        if stale or processing or dedup or queued:
            self._logger.info(
                "V3 reliability recovery: stale_leases=%s processing=%s dedup=%s queue=%s",
                stale,
                processing,
                dedup,
                queued,
            )

    async def _recovery_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.interval_seconds,
                )
            except TimeoutError:
                try:
                    await self._recover_once()
                except Exception:
                    self._logger.exception("V3 reliability recovery cycle failed")
            except asyncio.CancelledError:
                raise
