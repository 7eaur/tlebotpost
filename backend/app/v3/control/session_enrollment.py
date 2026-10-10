"""Secure in-process enrollment for the V3 Telegram user session."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.telegram.session import (
    TelegramSession,
    TelegramSessionError,
    TelegramSessionPasswordNeeded,
    normalize_phone,
)


class SessionEnrollmentError(RuntimeError):
    """Safe owner-facing session enrollment error code."""


@dataclass(slots=True)
class _PendingLogin:
    phone: str
    phone_code_hash: str
    awaiting_password: bool = False


class SessionEnrollmentServiceV3:
    """Authorize the configured Telethon session while the V3 runtime stays online."""

    def __init__(
        self,
        *,
        session: TelegramSession,
        connected: Callable[[], bool],
        on_authorized: Callable[[], Awaitable[None]],
    ) -> None:
        self.session = session
        self.connected = connected
        self.on_authorized = on_authorized
        self._pending: _PendingLogin | None = None

    @property
    def pending(self) -> bool:
        return self._pending is not None

    @property
    def awaiting_password(self) -> bool:
        return bool(self._pending and self._pending.awaiting_password)

    async def begin(self, phone: str) -> str:
        if self.connected():
            raise SessionEnrollmentError("session_already_connected")
        normalized = normalize_phone(phone)
        try:
            request = await self.session.request_login_code(normalized)
        except Exception as exc:
            raise SessionEnrollmentError("session_code_request_failed") from exc
        phone_code_hash = getattr(request, "phone_code_hash", None)
        if not isinstance(phone_code_hash, str) or not phone_code_hash:
            raise SessionEnrollmentError("session_code_request_failed")
        self._pending = _PendingLogin(
            phone=normalized,
            phone_code_hash=phone_code_hash,
        )
        return "code_requested"

    async def submit_code(self, code: str) -> str:
        pending = self._pending
        if pending is None:
            raise SessionEnrollmentError("session_enrollment_not_started")
        try:
            await self.session.complete_login(
                phone=pending.phone,
                code=code,
                phone_code_hash=pending.phone_code_hash,
            )
        except TelegramSessionPasswordNeeded:
            pending.awaiting_password = True
            return "password_required"
        except (TelegramSessionError, ValueError) as exc:
            raise SessionEnrollmentError("session_code_invalid") from exc
        except Exception as exc:
            raise SessionEnrollmentError("session_login_failed") from exc
        await self._finish_authorized()
        return "authorized"

    async def submit_password(self, password: str) -> str:
        pending = self._pending
        if pending is None or not pending.awaiting_password:
            raise SessionEnrollmentError("session_password_not_expected")
        try:
            await self.session.complete_login_password(password=password)
        except (TelegramSessionError, ValueError) as exc:
            raise SessionEnrollmentError("session_password_invalid") from exc
        except Exception as exc:
            raise SessionEnrollmentError("session_login_failed") from exc
        await self._finish_authorized()
        return "authorized"

    async def cancel(self) -> None:
        self._pending = None
        if not self.connected():
            try:
                await self.session.disconnect()
            except Exception:
                pass

    async def _finish_authorized(self) -> None:
        self._pending = None
        try:
            await self.on_authorized()
        except Exception as exc:
            raise SessionEnrollmentError("session_authorized_runtime_start_failed") from exc
