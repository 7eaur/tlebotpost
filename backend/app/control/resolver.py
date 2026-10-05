"""Resolve Telegram references for source and target configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

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
    """Resolve public/private chats using the authorized user client."""

    def __init__(self, client: Any) -> None:
        self.client = client

    async def resolve(self, input_ref: str) -> ResolvedChat:
        """Resolve a chat and take its current newest message as baseline."""
        normalized = normalize_chat_reference(input_ref)
        if not normalized:
            raise ValueError("مرجع القناة لا يمكن أن يكون فارغًا")
        if normalized.startswith("invite:"):
            raise ValueError(
                "رابط الدعوة الخاص يحتاج أن يكون الحساب عضوًا في القناة أولًا؛ "
                "أرسل الرابط بعد الانضمام أو استخدم @username للقناة العامة"
            )
        is_connected = getattr(self.client, "is_connected", None)
        if callable(is_connected) and not is_connected():
            await self.client.connect()
        is_authorized = getattr(self.client, "is_user_authorized", None)
        if callable(is_authorized) and not await is_authorized():
            raise TelegramSessionNotAuthorized(
                "سجّل جلسة حساب Telegram أولًا من زر تسجيل جلسة الحساب"
            )
        entity = await self.client.get_entity(normalized)
        title = getattr(entity, "title", None) or getattr(entity, "first_name", None)
        if not title:
            raise ValueError("المرجع لا يشير إلى مجموعة أو قناة مدعومة")
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


def normalize_chat_reference(input_ref: str) -> str:
    """Convert common Telegram URLs into a value Telethon can resolve."""
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
        return max((getattr(message, "id", 0) or 0 for message in messages), default=0)
    return getattr(messages, "id", 0) or 0
