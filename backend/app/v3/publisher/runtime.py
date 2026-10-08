"""Background V3 publisher worker over durable queue leases."""

from __future__ import annotations

import asyncio
import logging

from app.v3.publish_queue import PublishQueueV3

from .contracts import BotApiV3
from .media import MediaStagerV3
from .service import (
    PartialPublishError,
    PermanentPublishError,
    PublisherError,
    RetryablePublishError,
    TelegramPublisherV3,
)


class PublisherWorkerComponent:
    """Claim V3 jobs and translate publisher outcomes into durable queue states."""

    name = "telegram-publisher"

    def __init__(
        self,
        *,
        queue: PublishQueueV3,
        publisher: TelegramPublisherV3,
        bot: BotApiV3,
        stager: MediaStagerV3,
        worker_id: str,
        poll_interval_seconds: float = 1.0,
        batch_size: int = 10,
        lease_seconds: int = 120,
    ) -> None:
        worker = worker_id.strip()
        if not worker:
            raise ValueError("worker_id is required")
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        self.queue = queue
        self.publisher = publisher
        self.bot = bot
        self.stager = stager
        self.worker_id = worker
        self.poll_interval_seconds = poll_interval_seconds
        self.batch_size = batch_size
        self.lease_seconds = lease_seconds
        self._logger = logging.getLogger(__name__)
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop_event.clear()
        await self.bot.start()
        removed = self.stager.cleanup_stale()
        if removed:
            self._logger.info("V3 publisher removed stale media staging directories: count=%s", removed)
        self._task = asyncio.create_task(self._loop(), name="v3-telegram-publisher")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        await self.bot.stop()

    async def run_once(self) -> int:
        jobs = await self.queue.claim_due(
            worker_id=self.worker_id,
            limit=self.batch_size,
            lease_seconds=self.lease_seconds,
        )
        for job in jobs:
            await self._process_job(job.id)
        return len(jobs)

    async def _process_job(self, job_id) -> None:
        heartbeat = asyncio.create_task(
            self._heartbeat(job_id),
            name=f"v3-publish-heartbeat-{job_id}",
        )
        try:
            try:
                result = await self.publisher.publish_claimed(
                    job_id,
                    worker_id=self.worker_id,
                )
            except RetryablePublishError as exc:
                status = await self.queue.mark_retry(
                    job_id,
                    worker_id=self.worker_id,
                    error_code=exc.code,
                    error_message=str(exc),
                    retry_after_seconds=exc.retry_after_seconds,
                    safe_after_publish_start=(exc.code == "telegram_retry_after"),
                )
                self._logger.warning(
                    "V3 publish retry scheduled: job_id=%s code=%s status=%s",
                    job_id,
                    exc.code,
                    status.value,
                )
            except PartialPublishError as exc:
                await self.queue.mark_failed(
                    job_id,
                    worker_id=self.worker_id,
                    error_code=exc.code,
                    error_message=str(exc),
                    telegram_message_ids=exc.telegram_message_ids,
                )
                self._logger.error(
                    "V3 partial publish failed closed: job_id=%s code=%s confirmed=%s",
                    job_id,
                    exc.code,
                    len(exc.telegram_message_ids),
                )
            except PermanentPublishError as exc:
                await self.queue.mark_failed(
                    job_id,
                    worker_id=self.worker_id,
                    error_code=exc.code,
                    error_message=str(exc),
                )
                self._logger.error(
                    "V3 publish permanently failed: job_id=%s code=%s",
                    job_id,
                    exc.code,
                )
            except asyncio.CancelledError:
                raise
            except PublisherError as exc:
                await self.queue.mark_failed(
                    job_id,
                    worker_id=self.worker_id,
                    error_code=exc.code,
                    error_message=str(exc),
                    telegram_message_ids=exc.telegram_message_ids,
                )
            except Exception as exc:
                status = await self.queue.mark_retry(
                    job_id,
                    worker_id=self.worker_id,
                    error_code="publisher_internal_error",
                    error_message=type(exc).__name__,
                )
                self._logger.exception(
                    "V3 publisher internal error: job_id=%s resulting_status=%s",
                    job_id,
                    status.value,
                )
            else:
                self._logger.info(
                    "V3 publish completed: job_id=%s messages=%s latency_ms=%s",
                    result.job_id,
                    len(result.telegram_message_ids),
                    result.latency_ms,
                )
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

    async def _heartbeat(self, job_id) -> None:
        interval = max(1.0, self.lease_seconds / 3)
        while True:
            await asyncio.sleep(interval)
            try:
                await self.queue.renew_lease(
                    job_id,
                    worker_id=self.worker_id,
                    lease_seconds=self.lease_seconds,
                )
            except Exception:
                self._logger.debug(
                    "V3 publisher lease heartbeat stopped: job_id=%s",
                    job_id,
                    exc_info=True,
                )
                return

    async def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                processed = await self.run_once()
                if processed == 0:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self.poll_interval_seconds,
                    )
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            except Exception:
                self._logger.exception("V3 publisher worker cycle failed")
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self.poll_interval_seconds,
                    )
                except TimeoutError:
                    continue
