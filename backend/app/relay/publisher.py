"""Telegram publisher for transformed messages and albums."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

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
    ) -> None:
        self.client = client
        self.settings = settings
        self.events = events

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
                await self.client.send_file(
                    config.target_chat_id, file_argument, caption=result.text
                )
            else:
                if not result.text:
                    return await self._skip(source, messages, "empty_after_cleaning")
                await self.client.send_message(config.target_chat_id, result.text)
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


def _first_id(messages: Sequence[Any]) -> int | None:
    return getattr(messages[0], "id", None) if messages else None


def _media_type(message: Any) -> str:
    for name in ("photo", "video", "audio", "voice", "document", "sticker"):
        if getattr(message, name, None) is not None:
            return name
    return "media"
