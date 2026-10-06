"""Telegram user-client lifecycle for the v2 ingestion layer."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.telegram.session import TelegramSession


@dataclass(frozen=True, slots=True)
class TelegramClientConfig:
    """Credentials and session location for one Telegram user account."""

    api_id: int
    api_hash: str
    session_path: Path


class TelegramClientManager:
    """Own one authorized Telethon user client without exposing session details."""

    def __init__(
        self, config: TelegramClientConfig, session: TelegramSession | None = None
    ) -> None:
        self.config = config
        self.session = session or TelegramSession(
            api_id=config.api_id,
            api_hash=config.api_hash,
            session_path=config.session_path,
        )
        self._client: Any | None = None

    @property
    def client(self) -> Any:
        if self._client is None:
            raise RuntimeError("Telegram client is not connected")
        return self._client

    @property
    def is_connected(self) -> bool:
        return self._client is not None and self._client.is_connected()

    async def connect(self) -> Any:
        """Connect an already-authorized user session."""
        self._client = await self.session.connect(require_authorized=True)
        return self._client

    async def ensure_connected(self) -> Any:
        if not self.is_connected:
            return await self.connect()
        return self.client

    async def disconnect(self) -> None:
        await self.session.disconnect()
        self._client = None

    async def run_until_disconnected(self) -> None:
        """Keep Telethon's event loop alive until the connection ends."""
        await self.ensure_connected()
        await self.client.run_until_disconnected()
