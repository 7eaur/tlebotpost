"""Telegram Bot publisher and worker for v2 publish jobs."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from contextlib import ExitStack
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from telegram import InputFile, InputMediaPhoto, InputMediaVideo
from telegram.error import RetryAfter

from app.db.models import ContentItem, ContentMedia, Destination, MediaStatus, PublishJob, Source


class PublishError(RuntimeError):
    """Raised when a job cannot be published successfully."""


class MediaUnavailableError(PublishError):
    """Raised when accepted media cannot be recovered for publishing."""


@dataclass(frozen=True, slots=True)
class BotPublishResult:
    telegram_message_id: int
    message_count: int = 1
    latency_ms: int = 0


class BotPublisher:
    """Load a job and publish its content through Telegram Bot API."""

    def __init__(
        self,
        bot: Any,
        session_factory: async_sessionmaker[AsyncSession],
        account_id: Any,
        *,
        client_manager: Any | None = None,
        retry_after_retries: int = 3,
        send_interval_seconds: float = 1.1,
        max_concurrent_sends: int = 4,
    ) -> None:
        if retry_after_retries < 0:
            raise ValueError("retry_after_retries must be non-negative")
        if send_interval_seconds < 0:
            raise ValueError("send_interval_seconds must be non-negative")
        if max_concurrent_sends <= 0:
            raise ValueError("max_concurrent_sends must be positive")
        self.bot = bot
        self.session_factory = session_factory
        self.account_id = account_id
        self.client_manager = client_manager
        self.retry_after_retries = retry_after_retries
        self.send_interval_seconds = send_interval_seconds
        self._send_semaphore = asyncio.Semaphore(max_concurrent_sends)
        self._chat_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._last_send_at: dict[int, float] = {}
        self._logger = logging.getLogger(__name__)

    async def publish_job(self, job_id: Any) -> BotPublishResult:
        started = time.monotonic()
        content, media, destination, source = await self._load_job(job_id)
        if not content.text_normalized and not media:
            raise PublishError("content is empty")

        if not media:
            result = await self._send_with_retry(
                self.bot.send_message,
                destination.telegram_chat_id,
                text=content.text_normalized or "",
                disable_web_page_preview=True,
            )
            result_ids = [_message_id(result)]
        elif len(media) > 1 and all(item.media_type in {"photo", "video"} for item in media):
            result_ids = await self._publish_album(
                destination.telegram_chat_id,
                content,
                media,
                source,
            )
        else:
            result_ids = await self._publish_media_sequential(
                destination.telegram_chat_id, content, media, source
            )

        result = BotPublishResult(
            telegram_message_id=result_ids[0],
            message_count=len(result_ids),
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        self._logger.info(
            "v2 publish succeeded: job_id=%s destination=%s messages=%s latency_ms=%s",
            job_id,
            destination.telegram_chat_id,
            result.message_count,
            result.latency_ms,
        )
        return result

    async def _load_job(
        self, job_id: Any
    ) -> tuple[ContentItem, list[ContentMedia], Destination, Source]:
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
            source = await session.scalar(
                select(Source).where(
                    Source.id == content.source_id,
                    Source.account_id == self.account_id,
                )
            )
            if source is None:
                raise PublishError("source is missing")
            media = list(
                (
                    await session.scalars(
                        select(ContentMedia)
                        .where(ContentMedia.content_item_id == content.id)
                        .order_by(ContentMedia.created_at, ContentMedia.id)
                    )
                ).all()
            )
            return content, media, destination, source

    async def _publish_album(
        self,
        chat_id: int,
        content: ContentItem,
        media: list[ContentMedia],
        source: Source,
    ) -> list[int]:
        payloads = [await self._resolve_media(item, content, source) for item in media]
        with ExitStack() as stack:
            items: list[Any] = []
            for index, (row, payload) in enumerate(zip(media, payloads, strict=True)):
                media_value = self._input_value(payload, row, stack)
                caption = (
                    content.text_normalized
                    if index == 0 and content.text_normalized
                    else None
                )
                if row.media_type == "photo":
                    items.append(InputMediaPhoto(media=media_value, caption=caption))
                else:
                    items.append(InputMediaVideo(media=media_value, caption=caption))
            results = await self._send_with_retry(self.bot.send_media_group, chat_id, media=items)
        ids = [_message_id(item) for item in results]
        if not ids:
            raise PublishError("Telegram media group returned no messages")
        return ids

    async def _publish_media_sequential(
        self,
        chat_id: int,
        content: ContentItem,
        media: list[ContentMedia],
        source: Source,
    ) -> list[int]:
        message_ids: list[int] = []
        for index, item in enumerate(media):
            payload = await self._resolve_media(item, content, source)
            method_name = _send_method(item.media_type)
            method = getattr(self.bot, method_name, None)
            if method is None:
                raise PublishError(f"Telegram Bot API method is unavailable: {method_name}")
            with ExitStack() as stack:
                media_value = self._input_value(payload, item, stack)
                kwargs = {_send_parameter(item.media_type): media_value}
                if index == 0 and content.text_normalized:
                    kwargs["caption"] = content.text_normalized
                result = await self._send_with_retry(method, chat_id, **kwargs)
            message_ids.append(_message_id(result))
        return message_ids

    async def _resolve_media(
        self,
        row: ContentMedia,
        content: ContentItem,
        source: Source,
    ) -> Path | bytes:
        if row.status != MediaStatus.DELETED and row.storage_key:
            path = Path(row.storage_key)
            if path.is_file():
                return path

        if self.client_manager is None:
            raise MediaUnavailableError(f"media {row.id} has no available storage payload")
        metadata = row.metadata_json or {}
        chat_id = int(metadata.get("telegram_chat_id") or source.telegram_chat_id)
        message_id = int(metadata.get("telegram_message_id") or content.telegram_message_id or 0)
        if not message_id:
            raise MediaUnavailableError(f"media {row.id} has no Telegram message reference")

        client = await self.client_manager.ensure_connected()
        message = await client.get_messages(chat_id, ids=message_id)
        if message is None:
            raise MediaUnavailableError(f"Telegram message {message_id} is unavailable")
        payload = await client.download_media(message, file=bytes)
        if not payload:
            raise MediaUnavailableError(f"Telegram media {message_id} could not be downloaded")
        return payload

    @staticmethod
    def _input_value(payload: Path | bytes, row: ContentMedia, stack: ExitStack) -> Any:
        file_name = row.original_file_name or f"{row.id}{_extension(row.media_type)}"
        if isinstance(payload, Path):
            handle = stack.enter_context(payload.open("rb"))
            return InputFile(handle, filename=file_name)
        buffer = BytesIO(payload)
        buffer.name = file_name
        stack.callback(buffer.close)
        return InputFile(buffer, filename=file_name)

    async def _send_with_retry(self, method: Any, *args: Any, **kwargs: Any) -> Any:
        if not args:
            raise PublishError("Telegram send call is missing chat_id")
        chat_id = int(args[0])
        async with self._send_semaphore:
            async with self._chat_locks[chat_id]:
                for attempt in range(self.retry_after_retries + 1):
                    await self._wait_for_slot(chat_id)
                    try:
                        result = await method(*args, **kwargs)
                    except RetryAfter as exc:
                        self._logger.warning(
                            "Telegram RetryAfter: chat_id=%s seconds=%s",
                            chat_id,
                            exc.retry_after,
                        )
                        if attempt >= self.retry_after_retries:
                            raise
                        await asyncio.sleep(float(exc.retry_after))
                    else:
                        self._last_send_at[chat_id] = time.monotonic()
                        return result
        raise PublishError("send retry loop ended unexpectedly")

    async def _wait_for_slot(self, chat_id: int) -> None:
        last_send = self._last_send_at.get(chat_id, 0.0)
        remaining = self.send_interval_seconds - (time.monotonic() - last_send)
        if remaining > 0:
            await asyncio.sleep(remaining)


class PublishWorker:
    """Run due jobs and translate publisher outcomes into queue states."""

    def __init__(
        self,
        queue: Any,
        publisher: BotPublisher,
        *,
        retry_delay_seconds: int = 60,
        max_attempts: int = 8,
        concurrency: int = 4,
        on_published: Any | None = None,
    ) -> None:
        if retry_delay_seconds <= 0:
            raise ValueError("retry_delay_seconds must be positive")
        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        if concurrency <= 0:
            raise ValueError("concurrency must be positive")
        self.queue = queue
        self.publisher = publisher
        self.retry_delay_seconds = retry_delay_seconds
        self.max_attempts = max_attempts
        self.concurrency = concurrency
        self.on_published = on_published
        self._logger = logging.getLogger(__name__)

    async def run_once(self, *, limit: int = 10) -> int:
        jobs = await self.queue.claim_due(limit=limit)
        semaphore = asyncio.Semaphore(self.concurrency)

        async def process(job: Any) -> None:
            async with semaphore:
                await self._process_job(job)

        if jobs:
            await asyncio.gather(*(process(job) for job in jobs))
        return len(jobs)

    async def _process_job(self, job: Any) -> None:
        try:
            result = await self.publisher.publish_job(job.id)
        except RetryAfter as exc:
            await self._retry_or_fail(
                job,
                "retry_after",
                str(exc),
                float(exc.retry_after),
            )
        except MediaUnavailableError as exc:
            await self._retry_or_fail(
                job,
                "media_unavailable",
                str(exc),
                self.retry_delay_seconds,
            )
        except Exception as exc:
            self._logger.exception("v2 publish failed: job_id=%s", job.id)
            await self._retry_or_fail(
                job,
                type(exc).__name__,
                str(exc),
                self.retry_delay_seconds,
            )
        else:
            await self.queue.mark_published(
                job.id,
                telegram_message_id=result.telegram_message_id,
                latency_ms=result.latency_ms,
            )
            if self.on_published is not None:
                try:
                    await self.on_published(job.content_item_id)
                except Exception:
                    self._logger.warning(
                        "post-publish cleanup failed: content_item_id=%s",
                        job.content_item_id,
                        exc_info=True,
                    )

    async def _retry_or_fail(
        self,
        job: Any,
        error_code: str,
        error_message: str,
        delay_seconds: float,
    ) -> None:
        from datetime import UTC, datetime, timedelta

        if job.attempt_count >= self.max_attempts:
            await self.queue.mark_failed(
                job.id,
                error_code=error_code,
                error_message=error_message,
            )
            return
        await self.queue.mark_retry(
            job.id,
            error_code=error_code,
            error_message=error_message,
            retry_at=datetime.now(UTC) + timedelta(seconds=max(1.0, delay_seconds)),
        )


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


def _extension(media_type: str) -> str:
    return {
        "photo": ".jpg",
        "video": ".mp4",
        "audio": ".mp3",
        "voice": ".ogg",
    }.get(media_type, ".bin")
