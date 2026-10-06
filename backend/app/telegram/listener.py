"""Live-only Telegram source listener."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from telethon import events

from app.models import Source
from app.relay.albums import AlbumCollector
from app.repositories.sources import SourceRepository

MessageBatchCallback = Callable[[Source, Sequence[Any]], Awaitable[Any]]


class SourceListener:
    """Listen for new messages from every enabled source independently."""

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
        self._running = False
        self._accept_events = False
        self._locks: dict[int, asyncio.Lock] = {}
        self._handlers: dict[int, Any] = {}
        self._logger = logging.getLogger(__name__)

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        """Register one explicit NewMessage handler per enabled source."""
        if self._running:
            return
        try:
            await self.rebaseline()
        except Exception:
            await self._remove_source_handlers()
            raise
        self._running = True
        self._logger.info(
            "source listener started: enabled_sources=%s",
            len(self._handlers),
        )

    async def stop(self) -> None:
        """Stop accepting events, flush pending albums, and unregister handlers."""
        self._accept_events = False
        try:
            await self.albums.close()
        finally:
            await self._remove_source_handlers()
            self._running = False

    async def rebaseline(self) -> None:
        """Refresh baselines and explicitly subscribe to every enabled source."""
        self._accept_events = False
        await self._remove_source_handlers()
        for source in await self.sources.list(enabled_only=True):
            latest_id = await self._latest_message_id(source.chat_id)
            self._logger.info(
                "source baseline set: chat_id=%s message_id=%s",
                source.chat_id,
                latest_id,
            )
            await self.sources.upsert(
                chat_id=source.chat_id,
                input_ref=source.input_ref,
                title=source.title,
                username=source.username,
                baseline_message_id=latest_id,
            )
            self._register_source_handler(source.chat_id)
        self._accept_events = True

    def _register_source_handler(self, chat_id: int) -> None:
        """Subscribe Telethon directly to one source chat."""
        if chat_id in self._handlers:
            return
        handler = self._make_source_handler(chat_id)
        self.client.add_event_handler(handler, events.NewMessage(chats=chat_id))
        self._handlers[chat_id] = handler
        self._logger.info("source handler registered: chat_id=%s", chat_id)

    def _make_source_handler(self, chat_id: int) -> Callable[[Any], Awaitable[None]]:
        async def handle(event: Any) -> None:
            await self._handle_event(event, expected_chat_id=chat_id)

        return handle

    async def _remove_source_handlers(self) -> None:
        """Remove all explicit source subscriptions."""
        for chat_id, handler in tuple(self._handlers.items()):
            try:
                self.client.remove_event_handler(handler)
            except Exception:
                self._logger.warning(
                    "source handler removal failed: chat_id=%s",
                    chat_id,
                    exc_info=True,
                )
        self._handlers.clear()

    async def _latest_message_id(self, chat_id: int) -> int:
        entity = await self.client.get_entity(chat_id)
        messages = await self.client.get_messages(entity, limit=1)
        if messages is None:
            return 0
        if isinstance(messages, Sequence) and not isinstance(messages, (str, bytes)):
            return max((getattr(item, "id", 0) or 0 for item in messages), default=0)
        return getattr(messages, "id", 0) or 0

    async def _handle_event(
        self,
        event: Any,
        *,
        expected_chat_id: int | None = None,
    ) -> None:
        """Queue one event and advance its cursor only after the batch succeeds."""
        if not self._accept_events:
            return
        message = getattr(event, "message", event)
        chat_id = getattr(event, "chat_id", None) or getattr(message, "chat_id", None)
        message_id = getattr(message, "id", None)
        if chat_id is None or message_id is None:
            return
        if expected_chat_id is not None and chat_id != expected_chat_id:
            self._logger.warning(
                "source handler chat mismatch: expected=%s actual=%s message_id=%s",
                expected_chat_id,
                chat_id,
                message_id,
            )
            return

        lock = self._locks.setdefault(chat_id, asyncio.Lock())
        async with lock:
            source = await self.sources.get_by_chat_id(chat_id)
            if source is None or not source.enabled:
                return
            if message_id <= source.baseline_message_id:
                return
            self._logger.info(
                "new source message accepted: chat_id=%s message_id=%s",
                chat_id,
                message_id,
            )
            completion = await self.albums.add(source, message)

        try:
            await completion
        except Exception:
            self._logger.exception(
                "source message processing failed: chat_id=%s message_id=%s",
                chat_id,
                message_id,
            )
            raise
        await self.sources.advance_baseline(chat_id, message_id)
        self._logger.info(
            "source message processed: chat_id=%s message_id=%s",
            chat_id,
            message_id,
        )
