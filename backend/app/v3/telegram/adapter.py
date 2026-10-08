"""Telegram user-client abstraction and Telethon implementation for V3."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any, Protocol

from telethon import events

from app.telegram.session import TelegramSession

RawEventCallback = Callable[[Any], Awaitable[None]]


class TelegramMessageUnavailable(RuntimeError):
    """Raised when an accepted source message can no longer be fetched."""


class TelegramMediaUnavailable(RuntimeError):
    """Raised when an accepted source message no longer has downloadable media."""


class TelegramUserAdapter(Protocol):
    @property
    def is_connected(self) -> bool: ...

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def latest_message_id(self, chat_id: int) -> int: ...

    async def download_message_media(
        self,
        chat_id: int,
        message_id: int,
        destination_dir: str | Path,
    ) -> Path: ...

    async def download_message_media(
        self,
        chat_id: int,
        message_id: int,
        destination_dir: str | Path,
    ) -> Path:
        if message_id <= 0:
            raise ValueError("message_id must be positive")
        client = self._require_client()
        entity = await client.get_entity(chat_id)
        message = await client.get_messages(entity, ids=message_id)
        if isinstance(message, Sequence) and not isinstance(message, (str, bytes)):
            message = next(
                (item for item in message if int(getattr(item, "id", 0) or 0) == message_id),
                None,
            )
        if message is None:
            raise TelegramMessageUnavailable(
                f"source message is unavailable: chat_id={chat_id} message_id={message_id}"
            )
        if getattr(message, "media", None) is None:
            raise TelegramMediaUnavailable(
                f"source message has no media: chat_id={chat_id} message_id={message_id}"
            )

        directory = Path(destination_dir)
        directory.mkdir(parents=True, exist_ok=True)
        downloaded = await client.download_media(message, file=str(directory))
        if not downloaded:
            raise TelegramMediaUnavailable(
                f"source media could not be downloaded: chat_id={chat_id} message_id={message_id}"
            )
        path = Path(downloaded)
        if not path.exists() or not path.is_file():
            raise TelegramMediaUnavailable(
                f"source media download path is unavailable: message_id={message_id}"
            )
        return path

    async def subscribe(self, chat_id: int, callback: RawEventCallback) -> Any: ...

    async def unsubscribe(self, token: Any) -> None: ...

    async def wait_disconnected(self) -> None: ...


class TelethonUserAdapter:
    """Thin V3 adapter over the existing authorized Telegram user session."""

    def __init__(
        self,
        *,
        api_id: int,
        api_hash: str,
        session_path: str | Path,
        session: TelegramSession | None = None,
    ) -> None:
        self.session = session or TelegramSession(
            api_id=api_id,
            api_hash=api_hash,
            session_path=session_path,
        )
        self._client: Any | None = None

    @property
    def is_connected(self) -> bool:
        return self._client is not None and bool(self._client.is_connected())

    async def connect(self) -> None:
        self._client = await self.session.connect(require_authorized=True)

    async def disconnect(self) -> None:
        await self.session.disconnect()
        self._client = None

    async def latest_message_id(self, chat_id: int) -> int:
        client = self._require_client()
        entity = await client.get_entity(chat_id)
        messages = await client.get_messages(entity, limit=1)
        if messages is None:
            return 0
        if isinstance(messages, Sequence) and not isinstance(messages, (str, bytes)):
            return max((int(getattr(item, "id", 0) or 0) for item in messages), default=0)
        return int(getattr(messages, "id", 0) or 0)

    async def subscribe(self, chat_id: int, callback: RawEventCallback) -> Any:
        client = self._require_client()

        async def handler(event: Any) -> None:
            await callback(event)

        client.add_event_handler(handler, events.NewMessage(chats=chat_id))
        return handler

    async def unsubscribe(self, token: Any) -> None:
        if self._client is not None:
            self._client.remove_event_handler(token)

    async def wait_disconnected(self) -> None:
        client = self._require_client()
        await client.disconnected

    def _require_client(self) -> Any:
        if not self.is_connected:
            raise RuntimeError("V3 Telegram user client is not connected")
        return self._client
