"""Small domain models shared by the database and future Telegram services."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class Source:
    """A Telegram channel monitored by the relay."""

    id: int
    chat_id: int
    input_ref: str
    title: str
    username: str | None
    enabled: bool
    baseline_message_id: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class RelaySettings:
    """Singleton configuration stored in SQLite."""

    target_ref: str | None
    target_chat_id: int | None
    brand_footer: str
    brand_link: str
    enabled: bool
    include_keywords: tuple[str, ...]
    exclude_keywords: tuple[str, ...]
    allowed_media_types: tuple[str, ...]
