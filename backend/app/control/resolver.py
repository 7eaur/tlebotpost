"""Resolve Telegram references for source and target configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from telethon.utils import get_peer_id


@dataclass(frozen=True, slots=True)
class ResolvedChat:
    """Resolved Telegram chat metadata without message content."""

    chat_id: int
    input_ref: str
    title: str
    username: str | None
    latest_message_id: int


class TelegramChatResolver:
    """Resolve public/private chats using the authorized user client."""

    def __init__(self, client: Any) -> None:
        self.client = client

    async def resolve(self, input_ref: str) -> ResolvedChat:
        """Resolve a chat and take its current newest message as baseline."""
        normalized = input_ref.strip()
        if not normalized:
            raise ValueError("chat reference must not be empty")
        entity = await self.client.get_entity(normalized)
        title = getattr(entity, "title", None) or getattr(entity, "first_name", None)
        if not title:
            raise ValueError("reference is not a supported group or channel")
        latest = await self.client.get_messages(entity, limit=1)
        latest_id = _latest_message_id(latest)
        username = getattr(entity, "username", None)
        return ResolvedChat(
            chat_id=get_peer_id(entity),
            input_ref=normalized,
            title=title,
            username=username,
            latest_message_id=latest_id,
        )


def _latest_message_id(messages: Any) -> int:
    if messages is None:
        return 0
    if isinstance(messages, (list, tuple)):
        return max((getattr(message, "id", 0) or 0 for message in messages), default=0)
    return getattr(messages, "id", 0) or 0
