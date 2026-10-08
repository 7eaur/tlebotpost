"""V3 Telegram publishing service over durable queue payloads."""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import timedelta
from typing import TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from telegram.error import (\n    BadRequest,\n    Forbidden,\n    NetworkError,\n    RetryAfter,\n    TelegramError,\n    TimedOut,\n)

from app.db.models import (
    Destination,
    DestinationStatus,
    JobStatus,
    PublishJob,
    RouteExecution,
    RouteExecutionStatus,
    RoutePublishPayload,
    RouteStatus,
    Source,
    SourceRoute,
)
from app.v3.content import ProcessedContent, RoutePayloadStore
from app.v3.publish_queue import PublishQueueV3

from .contracts import (
    TELEGRAM_CAPTION_LIMIT,
    BotApiV3,
    PublisherResult,
    StagedMedia,
    album_family,
    split_telegram_text,
)
from .media import MediaAcquisitionError, MediaStagerV3

_T = TypeVar("_T")


class PublisherError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retry_after_seconds: int | None = None,
        telegram_message_ids: tuple[int, ...] = (),
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retry_after_seconds = retry_after_seconds
        self.telegram_message_ids = telegram_message_ids


class RetryablePublishError(PublisherError):
    """Failure that is safe to retry because no target message was confirmed."""


class PermanentPublishError(PublisherError):
    """Failure that should not be retried automatically."""


class PartialPublishError(PermanentPublishError):
    """At least one target message was confirmed before the attempt failed."""


@dataclass(frozen=True, slots=True)
class _PublishContext:
    job_id: uuid.UUID
    attempt_count: int
    source_chat_id: int
    destination_chat_id: int
    content: ProcessedContent


class TelegramPublisherV3:
    """Publish one already-claimed V3 job through the Bot API."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        account_id: uuid.UUID,
        queue: PublishQueueV3,
        bot: BotApiV3,
        stager: MediaStagerV3,
    ) -> None:
        self.session_factory = session_factory
        self.account_id = account_id
        self.queue = queue
        self.bot = bot
        self.stager = stager
        self.payloads = RoutePayloadStore()

    async def publish_claimed(
        self,
        job_id: uuid.UUID,
        *,
        worker_id: str,
    ) -> PublisherResult:
        context = await self._load_context(job_id, worker_id)
        staged: tuple[StagedMedia, ...] = ()
        if context.content.media:
            try:
                staged = await self.stager.stage(
                    job_id=context.job_id,
                    attempt_count=context.attempt_count,
                    chat_id=context.source_chat_id,
                    media=context.content.media,
                )
            except MediaAcquisitionError as exc:
                if exc.retryable:
                    raise RetryablePublishError(
                        exc.code,
                        str(exc),
                        retry_after_seconds=exc.retry_after_seconds,
                    ) from exc
                raise PermanentPublishError(exc.code, str(exc)) from exc

        try:
            await self.queue.mark_publishing(job_id, worker_id=worker_id)
            started = time.monotonic()
            sent: list[int] = []
            try:
                await self._send_content(context, staged, sent)
            except PublisherError:
                raise
            except Exception as exc:
                raise self._classify_bot_error(exc, tuple(sent)) from exc

            latency_ms = max(0, int((time.monotonic() - started) * 1000))
            if not sent:
                raise PermanentPublishError(
                    "telegram_no_messages_sent",
                    "publisher completed without a target message",
                )
            try:
                await self.queue.mark_published(
                    job_id,
                    worker_id=worker_id,
                    telegram_message_ids=tuple(sent),
                    latency_ms=latency_ms,
                )
            except Exception as exc:
                raise PartialPublishError(
                    "publish_persistence_failed",
                    "target accepted the publication but its durable completion failed",
                    telegram_message_ids=tuple(sent),
                ) from exc

            return PublisherResult(
                job_id=job_id,
                telegram_message_ids=tuple(sent),
                latency_ms=latency_ms,
            )
        finally:
            if context.content.media:
                self.stager.cleanup(
                    job_id=context.job_id,
                    attempt_count=context.attempt_count,
                )

    async def _load_context(self, job_id: uuid.UUID, worker_id: str) -> _PublishContext:
        async with self.session_factory() as session:
            job = await session.scalar(
                select(PublishJob).where(
                    PublishJob.id == job_id,
                    PublishJob.account_id == self.account_id,
                )
            )
            if job is None:
                raise PermanentPublishError("publish_job_unavailable", "publish job is unavailable")
            if job.route_execution_id is None or job.route_payload_id is None:
                raise PermanentPublishError("publish_job_not_v3", "publish job is not a V3 job")
            if job.status not in {JobStatus.PROCESSING, JobStatus.PUBLISHING}:
                raise PermanentPublishError(
                    "publish_job_not_claimed",
                    "publish job is not in a claimed state",
                )
            if job.locked_by != worker_id:
                raise PermanentPublishError(
                    "publish_job_owned_by_another_worker",
                    "publish job belongs to another worker",
                )

            execution = await session.scalar(
                select(RouteExecution).where(
                    RouteExecution.id == job.route_execution_id,
                    RouteExecution.account_id == self.account_id,
                )
            )
            payload = await session.scalar(
                select(RoutePublishPayload).where(
                    RoutePublishPayload.id == job.route_payload_id,
                    RoutePublishPayload.account_id == self.account_id,
                )
            )
            destination = await session.scalar(
                select(Destination).where(
                    Destination.id == job.destination_id,
                    Destination.account_id == self.account_id,
                )
            )
            route = await session.scalar(
                select(SourceRoute).where(
                    SourceRoute.id == job.source_route_id,
                    SourceRoute.account_id == self.account_id,
                )
            )
            if execution is None or payload is None or destination is None or route is None:
                raise PermanentPublishError(
                    "publish_dependencies_missing",
                    "publish job dependencies are unavailable",
                )
            source = await session.scalar(
                select(Source).where(
                    Source.id == execution.source_id,
                    Source.account_id == self.account_id,
                )
            )
            if source is None:
                raise PermanentPublishError("publish_source_missing", "source is unavailable")
            if destination.status is not DestinationStatus.ACTIVE:
                raise PermanentPublishError(
                    "publish_destination_inactive",
                    "destination is not active",
                )
            if route.status is not RouteStatus.ACTIVE:
                raise PermanentPublishError("publish_route_inactive", "route is not active")
            if execution.status is not RouteExecutionStatus.QUEUED:
                raise PermanentPublishError(
                    "publish_execution_not_queued",
                    "route execution is not queued",
                )

            content = self.payloads.to_content(payload)
            if not content.rendered_text and not content.media:
                raise PermanentPublishError("publish_empty_content", "publish payload is empty")
            return _PublishContext(
                job_id=job.id,
                attempt_count=job.attempt_count,
                source_chat_id=source.telegram_chat_id,
                destination_chat_id=destination.telegram_chat_id,
                content=content,
            )

    async def _send_content(
        self,
        context: _PublishContext,
        media: tuple[StagedMedia, ...],
        sent: list[int],
    ) -> None:
        text = context.content.rendered_text.strip()
        if not media:
            for chunk in split_telegram_text(text):
                sent.append(
                    await self._call_bot(
                        self.bot.send_text(context.destination_chat_id, chunk),
                        sent,
                    )
                )
            return

        caption = text if text and len(text) <= TELEGRAM_CAPTION_LIMIT else None
        if len(media) == 1:
            sent.append(
                await self._call_bot(
                    self.bot.send_media(
                        context.destination_chat_id,
                        media[0],
                        caption=caption,
                    ),
                    sent,
                )
            )
        else:
            try:
                album_family([item.media_type for item in media])
            except ValueError as exc:
                raise PermanentPublishError(
                    "telegram_album_unsupported",
                    str(exc),
                ) from exc
            sent.extend(
                await self._call_bot(
                    self.bot.send_album(
                        context.destination_chat_id,
                        media,
                        caption=caption,
                    ),
                    sent,
                )
            )

        if text and caption is None:
            for chunk in split_telegram_text(text):
                sent.append(
                    await self._call_bot(
                        self.bot.send_text(context.destination_chat_id, chunk),
                        sent,
                    )
                )

    async def _call_bot(
        self,
        operation: Awaitable[_T],
        sent: list[int],
    ) -> _T:
        try:
            return await operation
        except Exception as exc:
            raise self._classify_bot_error(exc, tuple(sent)) from exc

    def _classify_bot_error(
        self,
        exc: Exception,
        sent: tuple[int, ...],
    ) -> PublisherError:
        if sent:
            return PartialPublishError(
                "partial_publish",
                "publication failed after at least one target message was confirmed",
                telegram_message_ids=sent,
            )
        if isinstance(exc, RetryAfter):
            return RetryablePublishError(
                "telegram_retry_after",
                "Telegram requested a retry delay",
                retry_after_seconds=_retry_after_seconds(exc.retry_after),
            )
        if isinstance(exc, (TimedOut, NetworkError, TimeoutError, ConnectionError, OSError)):
            return RetryablePublishError(
                "telegram_network_error",
                type(exc).__name__,
            )
        if isinstance(exc, Forbidden):
            return PermanentPublishError("telegram_forbidden", "Telegram denied target access")
        if isinstance(exc, BadRequest):
            return PermanentPublishError("telegram_bad_request", "Telegram rejected the request")
        if isinstance(exc, TelegramError):
            return RetryablePublishError("telegram_error", type(exc).__name__)
        return PermanentPublishError("telegram_publish_error", type(exc).__name__)


def _retry_after_seconds(value: object) -> int:
    if isinstance(value, timedelta):
        seconds = int(value.total_seconds())
    else:
        seconds = int(value)
    return max(1, seconds)
