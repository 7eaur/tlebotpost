"""Relay runtime lifecycle and reconnect supervision."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

StatusCallback = Callable[[str], Awaitable[None]]


class RelayRuntime:
    """Start, supervise, and stop the user session and live listener together."""

    def __init__(
        self,
        session: Any,
        listener: Any,
        *,
        reconnect_delays: tuple[float, ...] = (5.0, 15.0, 30.0, 60.0),
        on_status: StatusCallback | None = None,
    ) -> None:
        if not reconnect_delays or any(delay <= 0 for delay in reconnect_delays):
            raise ValueError("reconnect_delays must contain positive values")
        self.session = session
        self.listener = listener
        self.reconnect_delays = reconnect_delays
        self.on_status = on_status
        self._running = False
        self._monitor_task: asyncio.Task[None] | None = None
        self._logger = logging.getLogger(__name__)

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        """Connect the session, establish live baselines, and start supervision."""
        if self._running:
            return
        await self.session.connect()
        await self.listener.start()
        self._running = True
        self._monitor_task = asyncio.create_task(self._monitor_connection())
        await self._notify("relay_started")

    async def stop(self) -> None:
        """Stop supervision, flush the listener, and disconnect the session."""
        if not self._running and self._monitor_task is None:
            return
        self._running = False
        if self._monitor_task:
            self._monitor_task.cancel()
            await asyncio.gather(self._monitor_task, return_exceptions=True)
            self._monitor_task = None
        try:
            await self.listener.stop()
        finally:
            await self.session.disconnect()
        await self._notify("relay_stopped")

    async def _monitor_connection(self) -> None:
        try:
            while self._running:
                await asyncio.sleep(5)
                if self._is_connected():
                    continue
                await self._recover_connection()
        except asyncio.CancelledError:
            raise

    async def _recover_connection(self) -> None:
        await self._notify("telegram_disconnected")
        for delay in self.reconnect_delays:
            if not self._running:
                return
            await asyncio.sleep(delay)
            try:
                await self.session.connect()
                await self.listener.rebaseline()
            except Exception as exc:
                self._logger.warning("Telegram reconnect failed: %s", type(exc).__name__)
                continue
            await self._notify("telegram_reconnected")
            return
        await self._notify("telegram_reconnect_retrying")

    def _is_connected(self) -> bool:
        checker = getattr(self.session.client, "is_connected", None)
        return bool(checker() if callable(checker) else checker)

    async def _notify(self, status: str) -> None:
        if self.on_status:
            await self.on_status(status)
