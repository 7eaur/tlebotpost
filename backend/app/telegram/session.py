"""User-account Telegram session lifecycle."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError


class TelegramSessionError(RuntimeError):
    """Base error for user session lifecycle failures."""


class TelegramSessionNotAuthorized(TelegramSessionError):
    """Raised when the local session is not logged into a user account."""


class TelegramSession:
    """Own one Telethon user client and keep session handling in one place."""

    def __init__(self, *, api_id: int, api_hash: str, session_path: str | Path) -> None:
        path = Path(session_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
        self.session_path = path
        self.client = TelegramClient(
            str(path),
            api_id,
            api_hash,
            device_model="Telegram Channel Relay",
            system_version="1.0",
            app_version="0.1.0",
            lang_code="ar",
        )

    async def connect(self, *, require_authorized: bool = True) -> TelegramClient:
        """Connect the client and optionally require an authorized user session."""
        await self.client.connect()
        if self.session_path.exists():
            os.chmod(self.session_path, 0o600)
        if require_authorized and not await self.client.is_user_authorized():
            raise TelegramSessionNotAuthorized(
                "Telegram user session is not authorized; complete login first"
            )
        return self.client

    async def request_login_code(self, phone: str) -> Any:
        """Send a login code; the caller must keep the returned hash private."""
        if not phone.strip():
            raise ValueError("phone must not be empty")
        if not self.client.is_connected():
            await self.client.connect()
        return await self.client.send_code_request(phone.strip())

    async def complete_login(
        self,
        *,
        phone: str,
        code: str,
        phone_code_hash: str,
        password: str | None = None,
    ) -> TelegramClient:
        """Complete code login and optionally handle two-step verification."""
        try:
            await self.client.sign_in(
                phone=phone.strip(),
                code=code.strip(),
                phone_code_hash=phone_code_hash,
            )
        except SessionPasswordNeededError as exc:
            if not password:
                raise TelegramSessionError(
                    "Telegram account requires a two-step verification password"
                ) from exc
            await self.client.sign_in(password=password)
        if not await self.client.is_user_authorized():
            raise TelegramSessionError("Telegram login did not authorize the user session")
        return self.client

    async def disconnect(self) -> None:
        """Disconnect cleanly without deleting the local session file."""
        if self.client.is_connected():
            await self.client.disconnect()
