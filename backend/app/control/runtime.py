"""Runtime lifecycle used by the control bot."""

from __future__ import annotations

from typing import Any


class RelayRuntime:
    """Start and stop the authorized user session and live listener together."""

    def __init__(self, session: Any, listener: Any) -> None:
        self.session = session
        self.listener = listener
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        """Connect the session and start listening to current sources."""
        if self._running:
            return
        await self.session.connect()
        await self.listener.start()
        self._running = True

    async def stop(self) -> None:
        """Stop listening and disconnect the user session."""
        if not self._running:
            return
        await self.listener.stop()
        await self.session.disconnect()
        self._running = False
