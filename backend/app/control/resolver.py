"""Backward-compatible imports for the shared Telegram chat resolver."""

from app.telegram.chat_resolver import (
    ResolvedChat,
    TelegramChatResolver,
    normalize_chat_reference,
)

__all__ = [
    "ResolvedChat",
    "TelegramChatResolver",
    "normalize_chat_reference",
]
