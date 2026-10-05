"""Short-window album aggregation for Telegram grouped media."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from app.models import Source

AlbumCallback = Callable[[Source, list[Any]], Awaitable[None]]


class AlbumCollector:
    """Collect grouped messages briefly and emit one batch per album."""

    def __init__(self, on_album: AlbumCallback, window_seconds: float = 0.8) -> None:
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self.on_album = on_album
        self.window_seconds = window_seconds
        self._pending: dict[tuple[int, int], tuple[Source, list[Any]]] = {}
        self._tasks: dict[tuple[int, int], asyncio.Task[None]] = {}

    async def add(self, source: Source, message: Any) -> None:
        """Queue an album item, or emit a normal message immediately."""
        grouped_id = getattr(message, "grouped_id", None)
        if grouped_id is None:
            await self.on_album(source, [message])
            return
        key = (source.chat_id, grouped_id)
        if key not in self._pending:
            self._pending[key] = (source, [])
            self._tasks[key] = asyncio.create_task(self._flush_later(key))
        self._pending[key][1].append(message)

    async def close(self) -> None:
        """Flush pending albums during a graceful shutdown."""
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        pending = list(self._pending.values())
        self._pending.clear()
        self._tasks.clear()
        for source, messages in pending:
            await self.on_album(source, messages)

    async def _flush_later(self, key: tuple[int, int]) -> None:
        try:
            await asyncio.sleep(self.window_seconds)
            source, messages = self._pending.pop(key)
            self._tasks.pop(key, None)
            await self.on_album(source, messages)
        except asyncio.CancelledError:
            raise
