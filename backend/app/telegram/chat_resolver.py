"""Resolve Telegram chat references using an authorized user client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from telethon import functions
from telethon.utils import get_peer_id

from app.telegram.session import TelegramSessionNotAuthorized


@dataclass(frozen=True, slots=True)
class ResolvedChat:
    """Resolved Telegram chat metadata without message content."""

    chat_id: int
    input_ref: str
    title: str
    username: str | None
    latest_message_id: int


class TelegramChatResolver:
    """Resolve public/private chats and capture the current newest message id."""

    def __init__(self, client: Any) -> None:
        self.client = client

    async def resolve(self, input_ref: str) -> ResolvedChat:
        normalized = normalize_chat_reference(input_ref)
        if not normalized:
            raise ValueError("Telegram chat reference cannot be empty")
        is_connected = getattr(self.client, "is_connected", None)
        if callable(is_connected) and not is_connected():
            await self.client.connect()
        is_authorized = getattr(self.client, "is_user_authorized", None)
        if callable(is_authorized) and not await is_authorized():
            raise TelegramSessionNotAuthorized("Telegram user session is not authorized")

        if normalized.startswith("invite:"):
            invite_hash = normalized.removeprefix("invite:")
            invite = await self.client(
                functions.messages.CheckChatInviteRequest(hash=invite_hash)
            )
            entity = getattr(invite, "chat", None)
            if entity is None:
                raise ValueError(
                    "invite is valid but the user session is not a member of the chat"
                )
        else:
            entity = await self.client.get_entity(normalized)

        title = getattr(entity, "title", None) or getattr(entity, "first_name", None)
        if not title:
            raise ValueError("reference does not resolve to a supported Telegram chat")
        latest = await self.client.get_messages(entity, limit=1)
        username = getattr(entity, "username", None)
        return ResolvedChat(
            chat_id=get_peer_id(entity),
            input_ref=normalized,
            title=str(title),
            username=str(username) if username else None,
            latest_message_id=_latest_message_id(latest),
        )


def normalize_chat_reference(input_ref: str) -> str:
    """Convert common Telegram URLs into values Telethon can resolve."""
    value = input_ref.strip()
    if not value:
        return ""
    if value.startswith("+"):
        return f"invite:{value[1:]}"

    candidate = value
    if not candidate.startswith(("http://", "https://")):
        if candidate.startswith(("t.me/", "telegram.me/")):
            candidate = f"https://{candidate}"
        else:
            return candidate

    parsed = urlsplit(candidate)
    if parsed.netloc.lower() not in {
        "t.me",
        "www.t.me",
        "telegram.me",
        "www.telegram.me",
    }:
        return value
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        return ""
    if parts[0] == "joinchat" or parts[0].startswith("+"):
        return f"invite:{parts[-1].lstrip('+')}"
    if parts[0] == "c" and len(parts) >= 2 and parts[1].isdigit():
        return f"-100{parts[1]}"
    if parts[0] == "s" and len(parts) >= 2:
        return f"@{parts[1].lstrip('@')}"
    return f"@{parts[0].lstrip('@')}"


def _latest_message_id(messages: Any) -> int:
    if messages is None:
        return 0
    if isinstance(messages, (list, tuple)):
        return max((int(getattr(message, "id", 0) or 0) for message in messages), default=0)
    return int(getattr(messages, "id", 0) or 0)
