"""Live-only Telegram listener for the v2 source-route architecture."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from telethon import events

from app.db import SourceBinding, SourceCheckpointRepository, SourceRouteRepository

from .v2_client import TelegramClientManager


@dataclass(frozen=True, slots=True)
class IngestionEvent:
    """A new Telegram message delivered to one active source route."""

    account_id: Any
    source_id: Any
    route_id: Any
    destination_id: Any
    chat_id: int
    message_id: int
    grouped_id: int | None
    message: Any
    received_at: datetime


IngestionCallback = Callable[[IngestionEvent], Awaitable[Any]]


class V2TelegramListener:
    """Subscribe to active routes and accept only messages after live cursors."""

    def __init__(
        self,
        client_manager: TelegramClientManager,
        session_factory: async_sessionmaker[AsyncSession],
        account_id: Any,
        on_message: IngestionCallback,
    ) -> None:
        self.client_manager = client_manager
        self.session_factory = session_factory
        self.account_id = account_id
        self.on_message = on_message
        self._running = False
        self._accept_events = False
        self._handlers: dict[int, Any] = {}
        self._bindings: dict[int, tuple[SourceBinding, ...]] = {}
        self._locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._logger = logging.getLogger(__name__)

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        """Load active routes, rebaseline live cursors, then register handlers."""
        if self._running:
            return
        client = await self.client_manager.ensure_connected()
        self._accept_events = False
        await self._remove_handlers()
        try:
            bindings = await self._load_active_bindings()
            grouped: dict[int, list[SourceBinding]] = defaultdict(list)
            for binding in bindings:
                grouped[binding.source.telegram_chat_id].append(binding)
            await self._rebaseline(client, grouped)
            self._bindings = {chat_id: tuple(items) for chat_id, items in grouped.items()}
            for chat_id in self._bindings:
                self._register_handler(client, chat_id)
        except Exception:
            await self._remove_handlers()
            self._bindings.clear()
            raise
        self._accept_events = True
        self._running = True
        self._logger.info("v2 Telegram listener started: sources=%s", len(self._bindings))

    async def stop(self) -> None:
        """Stop accepting events and remove every explicit source handler."""
        self._accept_events = False
        await self._remove_handlers()
        self._bindings.clear()
        self._running = False

    async def reload(self) -> None:
        """Reload active routes after source/route settings change."""
        was_running = self._running
        await self.stop()
        if was_running:
            await self.start()

    async def _load_active_bindings(self) -> Sequence[SourceBinding]:
        async with self.session_factory() as session:
            repository = SourceRouteRepository(session, self.account_id)
            return await repository.list_active_bindings()

    async def _rebaseline(
        self,
        client: Any,
        grouped: dict[int, list[SourceBinding]],
    ) -> None:
        async with self.session_factory() as session:
            checkpoints = SourceCheckpointRepository(session)
            async with session.begin():
                for chat_id, bindings in grouped.items():
                    latest_id = await self._latest_message_id(client, chat_id)
                    for binding in bindings:
                        await checkpoints.advance(binding.source.id, latest_id)
                    self._logger.info(
                        "v2 source baseline set: chat_id=%s message_id=%s routes=%s",
                        chat_id,
                        latest_id,
                        len(bindings),
                    )

    async def _latest_message_id(self, client: Any, chat_id: int) -> int:
        entity = await client.get_entity(chat_id)
        messages = await client.get_messages(entity, limit=1)
        if messages is None:
            return 0
        if isinstance(messages, Sequence) and not isinstance(messages, (str, bytes)):
            return max((getattr(item, "id", 0) or 0 for item in messages), default=0)
        return getattr(messages, "id", 0) or 0

    def _register_handler(self, client: Any, chat_id: int) -> None:
        handler = self._make_handler(chat_id)
        client.add_event_handler(handler, events.NewMessage(chats=chat_id))
        self._handlers[chat_id] = handler
        self._logger.info("v2 source handler registered: chat_id=%s", chat_id)

    def _make_handler(self, chat_id: int) -> Callable[[Any], Awaitable[None]]:
        async def handle(event: Any) -> None:
            await self.handle_event(event, expected_chat_id=chat_id)

        return handle

    async def _remove_handlers(self) -> None:
        client = self.client_manager.client if self.client_manager.is_connected else None
        if client is None:
            self._handlers.clear()
            return
        for chat_id, handler in tuple(self._handlers.items()):
            try:
                client.remove_event_handler(handler)
            except Exception:
                self._logger.warning(
                    "v2 source handler removal failed: chat_id=%s",
                    chat_id,
                    exc_info=True,
                )
        self._handlers.clear()

    async def handle_event(self, event: Any, *, expected_chat_id: int | None = None) -> None:
        """Deliver one event to all routes after the source checkpoint."""
        if not self._accept_events:
            return
        message = getattr(event, "message", event)
        chat_id = getattr(event, "chat_id", None) or getattr(message, "chat_id", None)
        message_id = getattr(message, "id", None)
        if chat_id is None or message_id is None:
            return
        if expected_chat_id is not None and chat_id != expected_chat_id:
            self._logger.warning(
                "v2 source handler chat mismatch: expected=%s actual=%s message_id=%s",
                expected_chat_id,
                chat_id,
                message_id,
            )
            return
        bindings = self._bindings.get(chat_id, ())
        if not bindings:
            return

        async with self._locks[chat_id]:
            async with self.session_factory() as read_session:
                checkpoint = await SourceCheckpointRepository(read_session).get(
                    bindings[0].source.id
                )
                if checkpoint is not None and message_id <= checkpoint.last_seen_message_id:
                    return
                await read_session.rollback()
            received_at = datetime.now(UTC)
            message_date = getattr(message, "date", None)
            if isinstance(message_date, datetime):
                received_at = message_date
            for binding in bindings:
                await self.on_message(
                    IngestionEvent(
                        account_id=self.account_id,
                        source_id=binding.source.id,
                        route_id=binding.route.id,
                        destination_id=binding.destination.id,
                        chat_id=chat_id,
                        message_id=message_id,
                        grouped_id=getattr(message, "grouped_id", None),
                        message=message,
                        received_at=received_at,
                    )
                )
            async with self.session_factory() as write_session:
                async with write_session.begin():
                    await SourceCheckpointRepository(write_session).advance(
                        bindings[0].source.id,
                        message_id,
                        event_at=received_at,
                    )
                self._logger.info(
                    "v2 new source message accepted: chat_id=%s message_id=%s routes=%s",
                    chat_id,
                    message_id,
                    len(bindings),
                )
