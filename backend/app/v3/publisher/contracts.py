"""Pure contracts and Telegram-safe text helpers for V3 publishing."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from collections.abc import Sequence
from typing import Protocol

TELEGRAM_TEXT_LIMIT = 4096
TELEGRAM_CAPTION_LIMIT = 1024
TELEGRAM_MEDIA_GROUP_LIMIT = 10


class PublishFailureKind(StrEnum):
    RETRYABLE = "retryable"
    PERMANENT = "permanent"
    PARTIAL = "partial"


@dataclass(frozen=True, slots=True)
class StagedMedia:
    media_type: str
    path: Path
    source_message_id: int
    file_name: str | None = None
    mime_type: str | None = None


@dataclass(frozen=True, slots=True)
class PublisherResult:
    job_id: uuid.UUID
    telegram_message_ids: tuple[int, ...]
    latency_ms: int

    @property
    def telegram_message_id(self) -> int:
        if not self.telegram_message_ids:
            raise ValueError("publisher result has no Telegram message id")
        return self.telegram_message_ids[0]


class BotApiV3(Protocol):
    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def send_text(self, chat_id: int, text: str) -> int: ...

    async def send_media(
        self,
        chat_id: int,
        media: StagedMedia,
        *,
        caption: str | None = None,
    ) -> int: ...

    async def send_album(
        self,
        chat_id: int,
        media: Sequence[StagedMedia],
        *,
        caption: str | None = None,
    ) -> tuple[int, ...]: ...


def split_telegram_text(text: str, limit: int = TELEGRAM_TEXT_LIMIT) -> tuple[str, ...]:
    """Split plain text deterministically without truncating content."""
    if limit <= 0:
        raise ValueError("limit must be positive")
    remaining = text.strip()
    if not remaining:
        return ()
    chunks: list[str] = []
    while len(remaining) > limit:
        boundary = max(
            remaining.rfind("\n", 0, limit + 1),
            remaining.rfind(" ", 0, limit + 1),
        )
        if boundary < max(1, limit // 3):
            boundary = limit
        chunk = remaining[:boundary].rstrip()
        if not chunk:
            chunk = remaining[:limit]
            boundary = limit
        chunks.append(chunk)
        remaining = remaining[boundary:].lstrip()
    if remaining:
        chunks.append(remaining)
    return tuple(chunks)


def album_family(media_types: Sequence[str]) -> str:
    """Return a Bot-API compatible album family or raise for unsupported mixes."""
    values = tuple(value.strip().lower() for value in media_types if value.strip())
    if len(values) < 2:
        raise ValueError("album requires at least two media items")
    if len(values) > TELEGRAM_MEDIA_GROUP_LIMIT:
        raise ValueError("album exceeds Telegram media group limit")
    if all(value in {"photo", "video"} for value in values):
        return "visual"
    if all(value == "document" for value in values):
        return "document"
    if all(value == "audio" for value in values):
        return "audio"
    raise ValueError("album media types cannot be represented by one Telegram media group")
