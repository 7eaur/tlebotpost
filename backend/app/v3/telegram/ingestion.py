"""Live-only Telegram ingestion component for V3."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    Destination,
    DestinationStatus,
    Project,
    ProjectStatus,
    RouteStatus,
    Source,
    SourceCheckpoint,
    SourceRoute,
    SourceStatus,
    TelegramAccount,
    TelegramAccountStatus,
)
from app.v3.domain import EventRegistration, RouteExecutionService

from .adapter import TelegramUserAdapter
from .albums import AlbumCollectorV3, AlbumSource
from .types import SourceEvent

RegistrationCallback = Callable[[SourceEvent, EventRegistration], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class IngestionSource:
    source_id: uuid.UUID
    chat_id: int


class TelegramIngestionComponent:
    """Subscribe active sources and persist one durable route registration per logical event."""

    name = "telegram-ingestion"

    def __init__(
        self,
        *,
        adapter: TelegramUserAdapter,
        session_factory: async_sessionmaker[AsyncSession],
        account_id: uuid.UUID,
        telegram_account_id: uuid.UUID,
        album_window_seconds: float = 0.8,
        reconnect_delays: tuple[float, ...] = (5.0, 15.0, 30.0, 60.0),
        on_registration: RegistrationCallback | None = None,
    ) -> None:
        if album_window_seconds <= 0:
            raise ValueError("album_window_seconds must be positive")
        if not reconnect_delays or any(delay <= 0 for delay in reconnect_delays):
            raise ValueError("reconnect_delays must contain positive values")
        self.adapter = adapter
        self.session_factory = session_factory
        self.account_id = account_id
        self.telegram_account_id = telegram_account_id
        self.reconnect_delays = reconnect_delays
        self.on_registration = on_registration
        self._collector = AlbumCollectorV3(
            self._persist_source_event,
            window_seconds=album_window_seconds,
        )
        self._sources_by_chat: dict[int, IngestionSource] = {}
        self._subscription_tokens: dict[int, Any] = {}
        self._seen_floor: dict[uuid.UUID, int] = {}
        self._source_locks: defaultdict[uuid.UUID, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._accept_events = False
        self._running = False
        self._watchdog_task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self._logger = logging.getLogger(__name__)

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if self._running:
            return
        self._stopping.clear()
        await self.adapter.connect()
        try:
            await self._reload_subscriptions(rebaseline=True)
        except Exception:
            await self.adapter.disconnect()
            raise
        self._accept_events = True
        self._running = True
        self._watchdog_task = asyncio.create_task(
            self._watch_connection(),
            name="v3-telegram-connection-watchdog",
        )
        self._logger.info("V3 Telegram ingestion started: sources=%s", len(self._sources_by_chat))

    async def stop(self) -> None:
        if not self._running and self._watchdog_task is None:
            if self.adapter.is_connected:
                await self.adapter.disconnect()
            return
        self._stopping.set()
        self._accept_events = False
        if self._watchdog_task is not None:
            self._watchdog_task.cancel()
            await asyncio.gather(self._watchdog_task, return_exceptions=True)
            self._watchdog_task = None
        await self._collector.close()
        await self._remove_subscriptions()
        await self.adapter.disconnect()
        self._sources_by_chat.clear()
        self._running = False
        self._logger.info("V3 Telegram ingestion stopped")

    async def flush_pending(self) -> None:
        """Durably register every pending logical album event."""
        await self._collector.flush_all()

    async def reload(self, *, rebaseline: bool = False) -> None:
        if not self.adapter.is_connected:
            raise RuntimeError("cannot reload V3 Telegram ingestion while disconnected")
        self._accept_events = False
        try:
            await self._collector.flush_all()
            await self._reload_subscriptions(rebaseline=rebaseline)
        finally:
            self._accept_events = self._running and not self._stopping.is_set()

    async def handle_raw_event(
        self,
        source: IngestionSource,
        event: Any,
        *,
        expected_chat_id: int | None = None,
    ) -> None:
        if not self._accept_events:
            return
        message = getattr(event, "message", event)
        chat_id = getattr(event, "chat_id", None) or getattr(message, "chat_id", None)
        message_id = int(getattr(message, "id", 0) or 0)
        if chat_id is None or message_id <= 0:
            return
        chat_id = int(chat_id)
        if expected_chat_id is not None and chat_id != expected_chat_id:
            self._logger.warning(
                "V3 Telegram source chat mismatch: expected=%s actual=%s message_id=%s",
                expected_chat_id,
                chat_id,
                message_id,
            )
            return
        if chat_id != source.chat_id:
            return

        if message_id <= self._seen_floor.get(source.source_id, 0):
            return
        await self._collector.add(
            AlbumSource(
                account_id=self.account_id,
                source_id=source.source_id,
                chat_id=source.chat_id,
            ),
            message,
        )

    async def _reload_subscriptions(self, *, rebaseline: bool) -> None:
        await self._remove_subscriptions()
        sources = await self._load_active_sources()
        new_sources: dict[int, IngestionSource] = {}
        for source in sources:
            if source.chat_id in new_sources and new_sources[source.chat_id] != source:
                raise RuntimeError(
                    "multiple V3 sources map to the same Telegram chat for one user session"
                )
            new_sources[source.chat_id] = source

        if rebaseline:
            for source in sources:
                latest_id = await self.adapter.latest_message_id(source.chat_id)
                await self._set_live_baseline(source.source_id, latest_id)
                self._seen_floor[source.source_id] = latest_id
                self._logger.info(
                    "V3 source live baseline set: source_id=%s message_id=%s",
                    source.source_id,
                    latest_id,
                )
        else:
            for source in sources:
                self._seen_floor[source.source_id] = await self._load_seen_floor(source.source_id)

        self._sources_by_chat = new_sources
        for source in sources:
            token = await self.adapter.subscribe(
                source.chat_id,
                self._handler_for(source),
            )
            self._subscription_tokens[source.chat_id] = token

    async def _load_active_sources(self) -> Sequence[IngestionSource]:
        async with self.session_factory() as session:
            statement = (
                select(Source.id, Source.telegram_chat_id)
                .join(SourceRoute, SourceRoute.source_id == Source.id)
                .join(Destination, Destination.id == SourceRoute.destination_id)
                .join(Project, Project.id == Destination.project_id)
                .join(TelegramAccount, TelegramAccount.id == Source.telegram_account_id)
                .where(
                    Source.account_id == self.account_id,
                    Source.telegram_account_id == self.telegram_account_id,
                    Source.status == SourceStatus.ACTIVE,
                    TelegramAccount.account_id == self.account_id,
                    TelegramAccount.status == TelegramAccountStatus.ACTIVE,
                    SourceRoute.account_id == self.account_id,
                    SourceRoute.status == RouteStatus.ACTIVE,
                    Destination.account_id == self.account_id,
                    Destination.status == DestinationStatus.ACTIVE,
                    Project.account_id == self.account_id,
                    Project.status == ProjectStatus.ACTIVE,
                )
                .distinct()
                .order_by(Source.id)
            )
            rows = (await session.execute(statement)).all()
            return tuple(
                IngestionSource(source_id=source_id, chat_id=int(chat_id))
                for source_id, chat_id in rows
            )

    async def _persist_source_event(self, event: SourceEvent) -> None:
        async with self._source_locks[event.source_id]:
            if event.cursor_message_id <= self._seen_floor.get(event.source_id, 0):
                return
            async with self.session_factory() as session:
                async with session.begin():
                    registration = await RouteExecutionService(
                        session,
                        self.account_id,
                    ).register_event(
                        source_id=event.source_id,
                        cursor_message_id=event.cursor_message_id,
                        telegram_message_id=event.primary_message_id,
                        telegram_grouped_id=event.grouped_id,
                    )
                    checkpoint = await session.scalar(
                        select(SourceCheckpoint)
                        .where(SourceCheckpoint.source_id == event.source_id)
                        .with_for_update()
                    )
                    if checkpoint is None:
                        checkpoint = SourceCheckpoint(
                            source_id=event.source_id,
                            last_seen_message_id=event.cursor_message_id,
                            last_committed_message_id=0,
                            last_event_at=event.received_at,
                        )
                        session.add(checkpoint)
                    elif event.cursor_message_id > checkpoint.last_seen_message_id:
                        checkpoint.last_seen_message_id = event.cursor_message_id
                        checkpoint.last_event_at = event.received_at
                    await session.flush()
            self._seen_floor[event.source_id] = event.cursor_message_id

        if self.on_registration is not None:
            await self.on_registration(event, registration)
        self._logger.info(
            "V3 source event registered: source_id=%s cursor=%s grouped=%s routes=%s",
            event.source_id,
            event.cursor_message_id,
            event.grouped_id is not None,
            len(registration.executions),
        )

    async def _set_live_baseline(self, source_id: uuid.UUID, latest_id: int) -> None:
        if latest_id < 0:
            raise ValueError("latest Telegram message id cannot be negative")
        async with self.session_factory() as session:
            async with session.begin():
                checkpoint = await session.scalar(
                    select(SourceCheckpoint)
                    .where(SourceCheckpoint.source_id == source_id)
                    .with_for_update()
                )
                if checkpoint is None:
                    checkpoint = SourceCheckpoint(
                        source_id=source_id,
                        last_seen_message_id=latest_id,
                        last_committed_message_id=0,
                    )
                    session.add(checkpoint)
                elif latest_id > checkpoint.last_seen_message_id:
                    checkpoint.last_seen_message_id = latest_id
                await session.flush()

    async def _load_seen_floor(self, source_id: uuid.UUID) -> int:
        async with self.session_factory() as session:
            checkpoint = await session.get(SourceCheckpoint, source_id)
            return checkpoint.last_seen_message_id if checkpoint is not None else 0

    def _handler_for(self, source: IngestionSource) -> Callable[[Any], Awaitable[None]]:
        async def handler(event: Any) -> None:
            await self.handle_raw_event(
                source,
                event,
                expected_chat_id=source.chat_id,
            )

        return handler

    async def _remove_subscriptions(self) -> None:
        for chat_id, token in tuple(self._subscription_tokens.items()):
            try:
                await self.adapter.unsubscribe(token)
            except Exception:
                self._logger.warning(
                    "V3 Telegram unsubscribe failed: chat_id=%s",
                    chat_id,
                    exc_info=True,
                )
        self._subscription_tokens.clear()

    async def _watch_connection(self) -> None:
        while not self._stopping.is_set():
            try:
                await self.adapter.wait_disconnected()
            except asyncio.CancelledError:
                raise
            except Exception:
                self._logger.warning("V3 Telegram disconnect watcher failed", exc_info=True)

            if self._stopping.is_set():
                return

            self._accept_events = False
            await self._collector.flush_all()
            await self._remove_subscriptions()
            self._logger.warning("V3 Telegram connection lost; reconnecting")

            reconnected = False
            for delay in self.reconnect_delays:
                if self._stopping.is_set():
                    return
                await asyncio.sleep(delay)
                try:
                    await self.adapter.connect()
                    await self._reload_subscriptions(rebaseline=True)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self._logger.warning(
                        "V3 Telegram reconnect attempt failed: delay=%s",
                        delay,
                        exc_info=True,
                    )
                    continue
                reconnected = True
                self._accept_events = True
                self._logger.info("V3 Telegram reconnected")
                break

            if not reconnected:
                self._logger.error("V3 Telegram reconnect delays exhausted")
                await asyncio.sleep(self.reconnect_delays[-1])
