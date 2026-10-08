"""Typed Telegram source events used by the V3 domain boundary."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class SourceMediaSnapshot:
    media_type: str
    identity: str
    file_name: str | None = None
    mime_type: str | None = None
    size: int | None = None


@dataclass(frozen=True, slots=True)
class SourceMessageSnapshot:
    id: int
    grouped_id: int | None
    date: datetime
    raw_text: str
    media: tuple[SourceMediaSnapshot, ...] = ()


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
    ) -> SourceEvent:
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

    def to_snapshot_messages(self) -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        for message in self.messages:
            message_id = int(getattr(message, "id", 0) or 0)
            raw_media = getattr(message, "media", None)
            candidates = (
                tuple(raw_media)
                if isinstance(raw_media, (list, tuple))
                else (() if raw_media is None else (raw_media,))
            )
            media = [
                _serialize_media(
                    item,
                    source_message_id=message_id,
                    item_index=index,
                )
                for index, item in enumerate(candidates)
            ]
            date = getattr(message, "date", None)
            if not isinstance(date, datetime):
                date = self.received_at
            if date.tzinfo is None:
                date = date.replace(tzinfo=UTC)
            result.append(
                {
                    "id": message_id,
                    "grouped_id": getattr(message, "grouped_id", None),
                    "date": date.astimezone(UTC).isoformat(),
                    "raw_text": _message_text(message),
                    "media": media,
                }
            )
        return result

    @classmethod
    def from_snapshot(
        cls,
        *,
        account_id: uuid.UUID,
        source_id: uuid.UUID,
        chat_id: int,
        messages: list[dict[str, object]],
        received_at: datetime,
    ) -> SourceEvent:
        restored: list[SourceMessageSnapshot] = []
        for item in messages:
            if not isinstance(item, dict):
                raise ValueError("snapshot message must be an object")
            message_id = item.get("id")
            if not isinstance(message_id, int) or message_id <= 0:
                raise ValueError("snapshot message id must be positive")
            grouped_id = item.get("grouped_id")
            if grouped_id is not None and not isinstance(grouped_id, int):
                raise ValueError("snapshot grouped_id must be an integer")
            raw_text = item.get("raw_text")
            if not isinstance(raw_text, str):
                raise ValueError("snapshot raw_text must be a string")
            date_value = item.get("date")
            if not isinstance(date_value, str):
                raise ValueError("snapshot date must be a string")
            date = datetime.fromisoformat(date_value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=UTC)
            media_value = item.get("media", [])
            if not isinstance(media_value, list):
                raise ValueError("snapshot media must be a list")
            media: list[SourceMediaSnapshot] = []
            for media_item in media_value:
                if not isinstance(media_item, dict):
                    raise ValueError("snapshot media item must be an object")
                media_type = media_item.get("media_type")
                identity = media_item.get("identity")
                if not isinstance(media_type, str) or not media_type:
                    raise ValueError("snapshot media type is required")
                if not isinstance(identity, str) or not identity:
                    raise ValueError("snapshot media identity is required")
                size = media_item.get("size")
                media.append(
                    SourceMediaSnapshot(
                        media_type=media_type,
                        identity=identity,
                        file_name=_optional_str(media_item.get("file_name")),
                        mime_type=_optional_str(media_item.get("mime_type")),
                        size=size if isinstance(size, int) and size >= 0 else None,
                    )
                )
            restored.append(
                SourceMessageSnapshot(
                    id=message_id,
                    grouped_id=grouped_id,
                    date=date.astimezone(UTC),
                    raw_text=raw_text,
                    media=tuple(media),
                )
            )

        event = cls.from_messages(
            account_id=account_id,
            source_id=source_id,
            chat_id=chat_id,
            messages=tuple(restored),
        )
        value = received_at if received_at.tzinfo is not None else received_at.replace(tzinfo=UTC)
        return cls(
            account_id=event.account_id,
            source_id=event.source_id,
            chat_id=event.chat_id,
            message_ids=event.message_ids,
            grouped_id=event.grouped_id,
            cursor_message_id=event.cursor_message_id,
            messages=event.messages,
            received_at=value.astimezone(UTC),
        )


def _message_text(message: Any) -> str:
    for name in ("raw_text", "message", "text", "caption"):
        value = getattr(message, name, None)
        if isinstance(value, str) and value:
            return value
    return ""


def _serialize_media(
    media: Any,
    *,
    source_message_id: int,
    item_index: int,
) -> dict[str, object]:
    media_type, payload = _classify_media(media)
    return {
        "media_type": media_type,
        "identity": _media_identity(
            media,
            payload,
            source_message_id=source_message_id,
            item_index=item_index,
            media_type=media_type,
        ),
        "file_name": _file_name(payload),
        "mime_type": _mime_type(payload),
        "size": _byte_size(payload),
    }


def _classify_media(media: Any) -> tuple[str, Any]:
    declared = getattr(media, "media_type", None) or getattr(media, "type", None)
    if isinstance(declared, str) and declared.strip():
        return declared.strip().lower(), media

    name = type(media).__name__.lower()
    if "photo" in name:
        return "photo", getattr(media, "photo", media)

    payload = getattr(media, "document", media)
    payload_name = type(payload).__name__.lower()
    mime = _mime_type(payload) or ""
    attributes = getattr(payload, "attributes", ()) or ()

    for attribute in attributes:
        attribute_name = type(attribute).__name__.lower()
        if "audio" in attribute_name:
            return ("voice" if bool(getattr(attribute, "voice", False)) else "audio"), payload
        if "video" in attribute_name:
            return "video", payload

    if mime.startswith("video/") or "video" in payload_name:
        return "video", payload
    if mime.startswith("audio/") or "audio" in payload_name:
        return "audio", payload
    if "voice" in payload_name:
        return "voice", payload
    if "document" in name or "document" in payload_name:
        return "document", payload
    return "unknown", payload


def _media_identity(
    media: Any,
    payload: Any,
    *,
    source_message_id: int,
    item_index: int,
    media_type: str,
) -> str:
    for candidate in (payload, media):
        identity = getattr(candidate, "identity", None)
        if isinstance(identity, str) and identity:
            return identity
        for name in ("file_unique_id", "id", "document_id"):
            value = getattr(candidate, name, None)
            if value not in (None, ""):
                return f"{name}:{value}"
    return f"source-message:{source_message_id}:{item_index}:{media_type}"


def _file_name(payload: Any) -> str | None:
    value = getattr(payload, "file_name", None)
    if isinstance(value, str) and value:
        return value
    for attribute in getattr(payload, "attributes", ()) or ():
        candidate = getattr(attribute, "file_name", None)
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


def _mime_type(payload: Any) -> str | None:
    value = getattr(payload, "mime_type", None)
    return value if isinstance(value, str) and value else None


def _byte_size(payload: Any) -> int | None:
    value = getattr(payload, "size", None)
    return int(value) if isinstance(value, int) and value >= 0 else None


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
