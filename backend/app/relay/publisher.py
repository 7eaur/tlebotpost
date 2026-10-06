"""Bot API publisher with temporary media transfer from the user session."""

from __future__ import annotations

import asyncio
import logging
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from telegram import (
    Bot,
    InputMediaAudio,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
)
from telegram.error import RetryAfter

from app.models import Source
from app.relay.transformer import ContentTransformer
from app.repositories.events import EventLogRepository
from app.repositories.settings import SettingsRepository


class PublisherConfigurationError(RuntimeError):
    """Raised when the target channel is not configured."""


class MediaDownloadError(RuntimeError):
    """Raised when the reader session cannot download source media."""


@dataclass(frozen=True, slots=True)
class PublishResult:
    """Result without storing the source message content."""

    status: str
    reason: str | None = None
    message_count: int = 0

    @property
    def published(self) -> bool:
        return self.status == "published"


class Publisher:
    """Transform with the user session, then publish through Bot API."""

    def __init__(
        self,
        reader_client: Any,
        bot: Bot,
        settings: SettingsRepository,
        events: EventLogRepository,
        *,
        retry_after_retries: int = 3,
        send_interval_seconds: float = 1.1,
    ) -> None:
        if retry_after_retries < 0:
            raise ValueError("retry_after_retries must be non-negative")
        if send_interval_seconds < 0:
            raise ValueError("send_interval_seconds must be non-negative")
        self.reader_client = reader_client
        self.bot = bot
        self.settings = settings
        self.events = events
        self.retry_after_retries = retry_after_retries
        self.send_interval_seconds = send_interval_seconds
        self._send_lock = asyncio.Lock()
        self._last_send_at = 0.0
        self._logger = logging.getLogger(__name__)

    async def publish(self, source: Source, messages: Sequence[Any]) -> PublishResult:
        """Transform and send one message or one album through Bot API."""
        started_at = time.monotonic()
        if not messages:
            return PublishResult("skipped", "empty_batch")
        config = await self.settings.get()
        if not config.enabled:
            return await self._skip(source, messages, "relay_disabled")
        if config.target_chat_id is None:
            raise PublisherConfigurationError("target channel is not configured")

        media = [
            message
            for message in messages
            if getattr(message, "media", None) is not None
            and _media_type(message) != "webpage"
        ]
        media_types = tuple(_media_type(message) for message in media)
        text = next(
            (
                getattr(message, "message", "") or ""
                for message in messages
                if getattr(message, "message", "")
            ),
            "",
        )
        result = ContentTransformer(config).transform(text, media_types=media_types)
        if not result.should_publish:
            return await self._skip(source, messages, result.skipped_reason or "filtered")

        try:
            if media:
                await self._publish_media(config.target_chat_id, media, result.text)
            elif result.text:
                self._logger.info(
                    "text publish started: source_chat_id=%s source_message_id=%s",
                    source.chat_id,
                    _first_id(messages),
                )
                await self._send_with_retry(
                    self.bot.send_message,
                    config.target_chat_id,
                    result.text,
                    disable_web_page_preview=True,
                )
            else:
                return await self._skip(source, messages, "empty_after_cleaning")
        except Exception as exc:
            await self.events.record(
                source_chat_id=source.chat_id,
                source_message_id=_first_id(messages),
                event_type="publish",
                status="failed",
                error_code=type(exc).__name__,
            )
            raise

        await self.events.record(
            source_chat_id=source.chat_id,
            source_message_id=_first_id(messages),
            event_type="publish",
            status="success",
        )
        self._logger.info(
            "publish completed: source_chat_id=%s source_message_id=%s elapsed_ms=%d",
            source.chat_id,
            _first_id(messages),
            int((time.monotonic() - started_at) * 1000),
        )
        return PublishResult("published", message_count=len(messages))

    async def _publish_media(
        self, target_chat_id: int, messages: Sequence[Any], caption: str | None
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="telegram-relay-") as directory:
            files = await self._download_media(messages, Path(directory))
            if len(files) == 1:
                await self._send_single_media(target_chat_id, messages[0], files[0], caption)
                return
            await self._send_album(target_chat_id, messages, files, caption)

    async def _download_media(
        self, messages: Sequence[Any], directory: Path
    ) -> list[Path]:
        downloaded: list[Path] = []
        for index, message in enumerate(messages):
            destination = directory / f"{index}-{getattr(message, 'id', index)}"
            path = await self.reader_client.download_media(message, file=str(destination))
            if not path:
                raise MediaDownloadError(
                    f"media download failed for message {getattr(message, 'id', index)}"
                )
            downloaded.append(Path(path))
        return downloaded

    async def _send_single_media(
        self, target_chat_id: int, message: Any, path: Path, caption: str | None
    ) -> None:
        media_type = _media_type(message)
        parameter = "photo" if media_type == "photo" else media_type
        if parameter not in {"photo", "video", "audio", "voice", "document"}:
            parameter = "document"
        with path.open("rb") as handle:
            method = getattr(self.bot, f"send_{parameter}")
            await self._send_with_retry(
                method, target_chat_id, **{parameter: handle}, caption=caption
            )

    async def _send_album(
        self,
        target_chat_id: int,
        messages: Sequence[Any],
        files: Sequence[Path],
        caption: str | None,
    ) -> None:
        for chunk_index in range(0, len(files), 10):
            chunk_files = files[chunk_index : chunk_index + 10]
            handles = [path.open("rb") for path in chunk_files]
            try:
                media = [
                    _input_media(
                        _media_type(messages[chunk_index + index]),
                        handle,
                        caption if chunk_index == 0 and index == 0 else None,
                    )
                    for index, handle in enumerate(handles)
                ]
                await self._send_with_retry(
                    self.bot.send_media_group, target_chat_id, media=media
                )
            finally:
                for handle in handles:
                    handle.close()

    async def _send_with_retry(self, method: Any, *args: Any, **kwargs: Any) -> Any:
        async with self._send_lock:
            for attempt in range(self.retry_after_retries + 1):
                await self._wait_for_send_slot()
                try:
                    result = await method(*args, **kwargs)
                except RetryAfter as exc:
                    self._logger.warning(
                        "Telegram Bot API requested retry: retry_after_seconds=%s",
                        exc.retry_after,
                    )
                    if attempt >= self.retry_after_retries:
                        raise
                    await asyncio.sleep(exc.retry_after)
                else:
                    self._last_send_at = time.monotonic()
                    return result
        raise RuntimeError("send retry loop ended unexpectedly")

    async def _wait_for_send_slot(self) -> None:
        elapsed = time.monotonic() - self._last_send_at
        remaining = self.send_interval_seconds - elapsed
        if remaining > 0:
            self._logger.info(
                "send rate limit waiting: wait_ms=%d", int(remaining * 1000)
            )
            await asyncio.sleep(remaining)

    async def _skip(
        self, source: Source, messages: Sequence[Any], reason: str
    ) -> PublishResult:
        await self.events.record(
            source_chat_id=source.chat_id,
            source_message_id=_first_id(messages),
            event_type="filter",
            status="skipped",
            error_code=reason,
        )
        return PublishResult("skipped", reason, len(messages))


def _first_id(messages: Sequence[Any]) -> int | None:
    return getattr(messages[0], "id", None) if messages else None


def _media_type(message: Any) -> str:
    media = getattr(message, "media", None)
    if media is not None and "webpage" in type(media).__name__.casefold():
        return "webpage"
    if getattr(media, "webpage", None) is not None:
        return "webpage"
    for name in ("photo", "video", "audio", "voice", "document", "sticker"):
        if getattr(message, name, None) is not None:
            return name
    return "media"


def _input_media(media_type: str, handle: Any, caption: str | None) -> Any:
    if media_type == "photo":
        return InputMediaPhoto(media=handle, caption=caption)
    if media_type == "video":
        return InputMediaVideo(media=handle, caption=caption)
    if media_type == "audio":
        return InputMediaAudio(media=handle, caption=caption)
    return InputMediaDocument(media=handle, caption=caption)
