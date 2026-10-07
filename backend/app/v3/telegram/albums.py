"""Ordered single-message and album aggregation for V3 Telegram ingestion."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .types import SourceEvent

SourceEventCallback = Callable[[SourceEvent], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class AlbumSource:
    account_id: uuid.UUID
    source_id: uuid.UUID
    chat_id: int


@dataclass(slots=True)
class _PendingAlbum:
    source: AlbumSource
    grouped_id: int
    messages: list[Any]


class AlbumCollectorV3:
    """Keep source ordering while converting Telegram grouped messages into one event."""

    def __init__(
        self,
        on_event: SourceEventCallback,
        *,
        window_seconds: float = 0.8,
    ) -> None:
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self.on_event = on_event
        self.window_seconds = window_seconds
        self._pending: dict[uuid.UUID, _PendingAlbum] = {}
        self._tasks: dict[uuid.UUID, asyncio.Task[None]] = {}
        self._locks: defaultdict[uuid.UUID, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._closed = False
        self._logger = logging.getLogger(__name__)

    async def add(self, source: AlbumSource, message: Any) -> None:
        if self._closed:
            raise RuntimeError("album collector is closed")
        grouped_id = getattr(message, "grouped_id", None)
        async with self._locks[source.source_id]:
            pending = self._pending.get(source.source_id)

            if grouped_id is None:
                if pending is not None:
                    await self._flush_locked(source.source_id)
                await self._emit(source, (message,))
                return

            grouped_id = int(grouped_id)
            if grouped_id <= 0:
                raise ValueError("grouped_id must be positive")

            if pending is not None and pending.grouped_id != grouped_id:
                await self._flush_locked(source.source_id)
                pending = None

            if pending is None:
                pending = _PendingAlbum(source=source, grouped_id=grouped_id, messages=[])
                self._pending[source.source_id] = pending
                self._tasks[source.source_id] = asyncio.create_task(
                    self._flush_later(source.source_id),
                    name=f"v3-album-{source.source_id}",
                )

            message_id = int(getattr(message, "id", 0) or 0)
            if message_id <= 0:
                raise ValueError("Telegram message id must be positive")
            if any(int(getattr(item, "id", 0) or 0) == message_id for item in pending.messages):
                return
            pending.messages.append(message)

    async def flush_all(self) -> None:
        current = asyncio.current_task()
        timer_tasks = [
            task for task in self._tasks.values()
            if task is not current
        ]
        for source_id in tuple(self._pending):
            async with self._locks[source_id]:
                if source_id in self._pending:
                    await self._flush_locked(source_id)
        if timer_tasks:
            await asyncio.gather(*timer_tasks, return_exceptions=True)

    async def close(self) -> None:
        if self._closed:
            return
        await self.flush_all()
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._closed = True

    async def _flush_later(self, source_id: uuid.UUID) -> None:
        try:
            await asyncio.sleep(self.window_seconds)
            async with self._locks[source_id]:
                if source_id in self._pending:
                    await self._flush_locked(source_id, cancel_timer=False)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._logger.warning(
                "V3 album persistence failed; keeping pending album: source_id=%s",
                source_id,
                exc_info=True,
            )
            if source_id in self._pending and not self._closed:
                self._tasks[source_id] = asyncio.create_task(
                    self._flush_later(source_id),
                    name=f"v3-album-retry-{source_id}",
                )

    async def _flush_locked(self, source_id: uuid.UUID, *, cancel_timer: bool = True) -> None:
        pending = self._pending[source_id]
        task = self._tasks.get(source_id)
        await self._emit(pending.source, tuple(pending.messages))
        self._pending.pop(source_id, None)
        self._tasks.pop(source_id, None)
        if cancel_timer and task is not None and task is not asyncio.current_task():
            task.cancel()

    async def _emit(self, source: AlbumSource, messages: tuple[Any, ...]) -> None:
        await self.on_event(
            SourceEvent.from_messages(
                account_id=source.account_id,
                source_id=source.source_id,
                chat_id=source.chat_id,
                messages=messages,
            )
        )
