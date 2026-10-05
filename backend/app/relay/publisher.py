"""Telegram publisher for transformed messages and albums."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from telethon.errors import FloodWaitError

from app.models import Source
from app.relay.transformer import ContentTransformer
from app.repositories.events import EventLogRepository
from app.repositories.settings import SettingsRepository


class PublisherConfigurationError(RuntimeError):
    """Raised when the target channel is not configured."""


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
    """Transform and publish content using a Telegram client."""

    def __init__(
        self,
        client: Any,
        settings: SettingsRepository,
        events: EventLogRepository,
        flood_wait_retries: int = 3,
    ) -> None:
        if flood_wait_retries < 0:
            raise ValueError("flood_wait_retries must be non-negative")
        self.client = client
        self.settings = settings
        self.events = events
        self.flood_wait_retries = flood_wait_retries

    async def publish(self, source: Source, messages: Sequence[Any]) -> PublishResult:
        """Publish one message or one collected album as a new message."""
        if not messages:
            return PublishResult("skipped", "empty_batch")
        config = await self.settings.get()
        if not config.enabled:
            return await self._skip(source, messages, "relay_disabled")
        if config.target_chat_id is None:
            raise PublisherConfigurationError("target channel is not configured")

        media = [message for message in messages if getattr(message, "media", None) is not None]
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
                files = [message.media for message in media]
                file_argument: Any = files if len(files) > 1 else files[0]
                await self._send_with_flood_wait(
                    self.client.send_file,
                    config.target_chat_id,
                    file_argument,
                    caption=result.text,
                )
            else:
                if not result.text:
                    return await self._skip(source, messages, "empty_after_cleaning")
                await self._send_with_flood_wait(
                    self.client.send_message, config.target_chat_id, result.text
                )
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
        return PublishResult("published", message_count=len(messages))

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

    async def _send_with_flood_wait(self, method: Any, *args: Any, **kwargs: Any) -> Any:
        for attempt in range(self.flood_wait_retries + 1):
            try:
                return await method(*args, **kwargs)
            except FloodWaitError as exc:
                if attempt >= self.flood_wait_retries:
                    raise
                wait_seconds = getattr(exc, "seconds", None)
                if wait_seconds is None:
                    raise
                await asyncio.sleep(wait_seconds)


def _first_id(messages: Sequence[Any]) -> int | None:
    return getattr(messages[0], "id", None) if messages else None


def _media_type(message: Any) -> str:
    for name in ("photo", "video", "audio", "voice", "document", "sticker"):
        if getattr(message, name, None) is not None:
            return name
    return "media"
