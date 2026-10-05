"""Live-only Telegram source listener."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from telethon import events

from app.models import Source
from app.relay.albums import AlbumCollector
from app.repositories.sources import SourceRepository

MessageBatchCallback = Callable[[Source, Sequence[Any]], Awaitable[None]]


class SourceListener:
    """Listen for new messages and deliberately skip historical messages."""

    def __init__(
        self,
        client: Any,
        sources: SourceRepository,
        on_message: MessageBatchCallback,
        album_window_seconds: float = 0.8,
    ) -> None:
        self.client = client
        self.sources = sources
        self.on_message = on_message
        self.albums = AlbumCollector(on_message, album_window_seconds)
        self._handler = self._handle_event
        self._running = False
        self._accept_events = False
        self._locks: dict[int, asyncio.Lock] = {}

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        """Register the event handler and establish current live baselines."""
        if self._running:
            return
        self.client.add_event_handler(self._handler, events.NewMessage())
        try:
            await self.rebaseline()
        except Exception:
            self.client.remove_event_handler(self._handler)
            raise
        self._running = True

    async def stop(self) -> None:
        """Stop accepting events, flush pending albums, and unregister the handler."""
        self._accept_events = False
        await self.albums.close()
        if self._running:
            self.client.remove_event_handler(self._handler)
        self._running = False

    async def rebaseline(self) -> None:
        """Set every enabled source cursor to its current newest message id.

        This is used at startup and after reconnect so downtime is never replayed.
        """
        self._accept_events = False
        for source in await self.sources.list(enabled_only=True):
            latest_id = await self._latest_message_id(source.chat_id)
            await self.sources.upsert(
                chat_id=source.chat_id,
                input_ref=source.input_ref,
                title=source.title,
                username=source.username,
                baseline_message_id=latest_id,
            )
        self._accept_events = True

    async def _latest_message_id(self, chat_id: int) -> int:
        entity = await self.client.get_entity(chat_id)
        messages = await self.client.get_messages(entity, limit=1)
        if messages is None:
            return 0
        if isinstance(messages, Sequence) and not isinstance(messages, (str, bytes)):
            return max((getattr(item, "id", 0) or 0 for item in messages), default=0)
        return getattr(messages, "id", 0) or 0

    async def _handle_event(self, event: Any) -> None:
        """Queue one event only when it is newer than the stored baseline."""
        if not self._accept_events:
            return
        message = getattr(event, "message", event)
        chat_id = getattr(event, "chat_id", None) or getattr(message, "chat_id", None)
        message_id = getattr(message, "id", None)
        if chat_id is None or message_id is None:
            return

        lock = self._locks.setdefault(chat_id, asyncio.Lock())
        async with lock:
            source = await self.sources.get_by_chat_id(chat_id)
            if source is None or not source.enabled:
                return
            if message_id <= source.baseline_message_id:
                return
            await self.albums.add(source, message)
            await self.sources.advance_baseline(chat_id, message_id)
