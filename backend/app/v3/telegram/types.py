"""Typed Telegram source events used by the V3 domain boundary."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class SourceEvent:
    account_id: uuid.UUID
    source_id: uuid.UUID
    chat_id: int
    message_ids: tuple[int, ...]
    grouped_id: int | None
    cursor_message_id: int
    messages: tuple[Any, ...]
    received_at: datetime

    @property
    def primary_message_id(self) -> int:
        return self.message_ids[0]

    @classmethod
    def from_messages(
        cls,
        *,
        account_id: uuid.UUID,
        source_id: uuid.UUID,
        chat_id: int,
        messages: tuple[Any, ...],
    ) -> "SourceEvent":
        if not messages:
            raise ValueError("source event requires at least one Telegram message")

        ordered = tuple(sorted(messages, key=lambda item: int(getattr(item, "id", 0) or 0)))
        message_ids = tuple(int(getattr(item, "id", 0) or 0) for item in ordered)
        if any(message_id <= 0 for message_id in message_ids):
            raise ValueError("Telegram message ids must be positive")

        grouped_values = {getattr(item, "grouped_id", None) for item in ordered}
        if len(grouped_values) != 1:
            raise ValueError("source event messages must share one grouped_id")
        grouped_id = grouped_values.pop()
        if len(ordered) > 1 and grouped_id is None:
            raise ValueError("multiple Telegram messages require grouped_id")

        received_at = datetime.now(UTC)
        dates = [getattr(item, "date", None) for item in ordered]
        valid_dates = [value for value in dates if isinstance(value, datetime)]
        if valid_dates:
            received_at = max(
                value if value.tzinfo is not None else value.replace(tzinfo=UTC)
                for value in valid_dates
            )

        return cls(
            account_id=account_id,
            source_id=source_id,
            chat_id=chat_id,
            message_ids=message_ids,
            grouped_id=grouped_id,
            cursor_message_id=max(message_ids),
            messages=ordered,
            received_at=received_at,
        )
