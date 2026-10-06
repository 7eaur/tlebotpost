"""Telegram Bot publisher for v2 publish jobs."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from telegram.error import RetryAfter

from app.db.models import ContentItem, ContentMedia, Destination, MediaStatus, PublishJob


class PublishError(RuntimeError):
    """Raised when a job cannot be published successfully."""


class MediaUnavailableError(PublishError):
    """Raised when accepted media has not been stored for scheduled publishing."""


@dataclass(frozen=True, slots=True)
class BotPublishResult:
    telegram_message_id: int
    message_count: int = 1
    latency_ms: int = 0


class BotPublisher:
    """Load a job and publish its stored content through python-telegram-bot."""

    def __init__(
        self,
        bot: Any,
        session_factory: async_sessionmaker[AsyncSession],
        account_id: Any,
        *,
        retry_after_retries: int = 3,
        send_interval_seconds: float = 1.1,
    ) -> None:
        if retry_after_retries < 0:
            raise ValueError("retry_after_retries must be non-negative")
        if send_interval_seconds < 0:
            raise ValueError("send_interval_seconds must be non-negative")
        self.bot = bot
        self.session_factory = session_factory
        self.account_id = account_id
        self.retry_after_retries = retry_after_retries
        self.send_interval_seconds = send_interval_seconds
        self._send_lock = asyncio.Lock()
        self._last_send_at = 0.0
        self._logger = logging.getLogger(__name__)

    async def publish_job(self, job_id: Any) -> BotPublishResult:
        started = time.monotonic()
        content, media, destination = await self._load_job(job_id)
        if not content.text_normalized and not media:
            raise PublishError("content is empty")
        if media:
            result_ids = await self._publish_media(destination.telegram_chat_id, content, media)
        else:
            result = await self._send_with_retry(
                self.bot.send_message,
                destination.telegram_chat_id,
                text=content.text_normalized or "",
                disable_web_page_preview=True,
            )
            result_ids = [_message_id(result)]
        return BotPublishResult(
            telegram_message_id=result_ids[0],
            message_count=len(result_ids),
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    async def _load_job(self, job_id: Any) -> tuple[ContentItem, list[ContentMedia], Destination]:
        async with self.session_factory() as session:
            job = await session.scalar(
                select(PublishJob).where(
                    PublishJob.id == job_id,
                    PublishJob.account_id == self.account_id,
                )
            )
            if job is None:
                raise PublishError("publish job is not available in this account")
            content = await session.scalar(
                select(ContentItem).where(
                    ContentItem.id == job.content_item_id,
                    ContentItem.account_id == self.account_id,
                )
            )
            destination = await session.scalar(
                select(Destination).where(
                    Destination.id == job.destination_id,
                    Destination.account_id == self.account_id,
                )
            )
            if content is None or destination is None:
                raise PublishError("publish job dependencies are missing")
            media = list(
                (
                    await session.scalars(
                        select(ContentMedia)
                        .where(ContentMedia.content_item_id == content.id)
                        .order_by(ContentMedia.created_at)
                    )
                ).all()
            )
            return content, media, destination

    async def _publish_media(
        self,
        chat_id: int,
        content: ContentItem,
        media: list[ContentMedia],
    ) -> list[int]:
        message_ids: list[int] = []
        for index, item in enumerate(media):
            if item.status == MediaStatus.DELETED or not item.storage_key:
                raise MediaUnavailableError(f"media {item.id} has no available storage_key")
            method_name = _send_method(item.media_type)
            method = getattr(self.bot, method_name, None)
            if method is None:
                raise PublishError(f"Telegram Bot API method is unavailable: {method_name}")
            kwargs = {_send_parameter(item.media_type): item.storage_key}
            if index == 0 and content.text_normalized:
                kwargs["caption"] = content.text_normalized
            result = await self._send_with_retry(method, chat_id, **kwargs)
            message_ids.append(_message_id(result))
        return message_ids

    async def _send_with_retry(self, method: Any, *args: Any, **kwargs: Any) -> Any:
        async with self._send_lock:
            for attempt in range(self.retry_after_retries + 1):
                await self._wait_for_slot()
                try:
                    result = await method(*args, **kwargs)
                except RetryAfter as exc:
                    self._logger.warning("Telegram RetryAfter: seconds=%s", exc.retry_after)
                    if attempt >= self.retry_after_retries:
                        raise
                    await asyncio.sleep(exc.retry_after)
                else:
                    self._last_send_at = time.monotonic()
                    return result
        raise PublishError("send retry loop ended unexpectedly")

    async def _wait_for_slot(self) -> None:
        remaining = self.send_interval_seconds - (time.monotonic() - self._last_send_at)
        if remaining > 0:
            await asyncio.sleep(remaining)


class PublishWorker:
    """Run due jobs and translate publisher outcomes into queue states."""

    def __init__(
        self, queue: Any, publisher: BotPublisher, *, retry_delay_seconds: int = 60
    ) -> None:
        if retry_delay_seconds <= 0:
            raise ValueError("retry_delay_seconds must be positive")
        self.queue = queue
        self.publisher = publisher
        self.retry_delay_seconds = retry_delay_seconds

    async def run_once(self, *, limit: int = 10) -> int:
        jobs = await self.queue.claim_due(limit=limit)
        for job in jobs:
            try:
                result = await self.publisher.publish_job(job.id)
            except RetryAfter as exc:
                from datetime import UTC, datetime, timedelta

                await self.queue.mark_retry(
                    job.id,
                    error_code="retry_after",
                    error_message=str(exc),
                    retry_at=datetime.now(UTC) + timedelta(seconds=exc.retry_after),
                )
            except MediaUnavailableError as exc:
                await self.queue.mark_failed(
                    job.id, error_code="media_unavailable", error_message=str(exc)
                )
            except Exception as exc:
                from datetime import UTC, datetime, timedelta

                await self.queue.mark_retry(
                    job.id,
                    error_code=type(exc).__name__,
                    error_message=str(exc),
                    retry_at=datetime.now(UTC) + timedelta(seconds=self.retry_delay_seconds),
                )
            else:
                await self.queue.mark_published(
                    job.id,
                    telegram_message_id=result.telegram_message_id,
                    latency_ms=result.latency_ms,
                )
        return len(jobs)


def _message_id(result: Any) -> int:
    value = getattr(result, "message_id", None) or getattr(result, "id", None)
    if value is None:
        raise PublishError("Telegram response did not contain a message id")
    return int(value)


def _send_method(media_type: str) -> str:
    return {
        "photo": "send_photo",
        "video": "send_video",
        "audio": "send_audio",
        "voice": "send_voice",
    }.get(media_type, "send_document")


def _send_parameter(media_type: str) -> str:
    return {
        "photo": "photo",
        "video": "video",
        "audio": "audio",
        "voice": "voice",
    }.get(media_type, "document")
