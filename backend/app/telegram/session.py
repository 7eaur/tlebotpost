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


class TelegramSessionPasswordNeeded(TelegramSessionError):
    """Raised when Telegram requires the account's two-step password."""


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
        normalized_phone = normalize_phone(phone)
        if not self.client.is_connected():
            await self.client.connect()
        return await self.client.send_code_request(normalized_phone)

    async def complete_login(
        self,
        *,
        phone: str,
        code: str,
        phone_code_hash: str,
        password: str | None = None,
    ) -> TelegramClient:
        """Complete code login and optionally handle two-step verification."""
        normalized_phone = normalize_phone(phone)
        normalized_code = normalize_code(code)
        try:
            await self.client.sign_in(
                phone=normalized_phone,
                code=normalized_code,
                phone_code_hash=phone_code_hash,
            )
        except SessionPasswordNeededError as exc:
            if not password:
                raise TelegramSessionPasswordNeeded(
                    "Telegram account requires a two-step verification password"
                ) from exc
            await self.client.sign_in(password=password)
        if not await self.client.is_user_authorized():
            raise TelegramSessionError("Telegram login did not authorize the user session")
        return self.client

    async def complete_login_password(self, *, password: str) -> TelegramClient:
        """Finish a login that paused for the account's two-step password."""
        if not password.strip():
            raise ValueError("كلمة مرور التحقق بخطوتين لا يمكن أن تكون فارغة")
        await self.client.sign_in(password=password)
        if not await self.client.is_user_authorized():
            raise TelegramSessionError("Telegram login did not authorize the user session")
        return self.client

    async def disconnect(self) -> None:
        """Disconnect cleanly without deleting the local session file."""
        if self.client.is_connected():
            await self.client.disconnect()


def normalize_phone(phone: str) -> str:
    """Normalize Arabic/Latin digits and common international phone formats."""
    translation = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
    value = phone.strip().translate(translation).replace(" ", "").replace("-", "")
    if value.startswith("00"):
        value = f"+{value[2:]}"
    if not value.startswith("+") or not value[1:].isdigit() or not 8 <= len(value[1:]) <= 15:
        raise ValueError("رقم الهاتف غير صالح. استخدم الصيغة الدولية مثل +967700000000")
    return value


def normalize_code(code: str) -> str:
    """Normalize Telegram login code digits entered in Arabic or Latin form."""
    translation = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
    value = code.strip().translate(translation).replace(" ", "")
    if not value.isdigit():
        raise ValueError("رمز الدخول يجب أن يتكون من أرقام فقط")
    return value
