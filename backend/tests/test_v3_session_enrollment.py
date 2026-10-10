from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.telegram.session import TelegramSessionPasswordNeeded
from app.v3.control import SessionEnrollmentError, SessionEnrollmentServiceV3


class FakeSession:
    def __init__(self, *, require_password: bool = False) -> None:
        self.require_password = require_password
        self.requested_phone: str | None = None
        self.code_calls = 0
        self.password_calls = 0
        self.disconnected = False

    async def request_login_code(self, phone: str):
        self.requested_phone = phone
        return SimpleNamespace(phone_code_hash="safe-test-hash")

    async def complete_login(
        self,
        *,
        phone: str,
        code: str,
        phone_code_hash: str,
        password: str | None = None,
    ):
        self.code_calls += 1
        if code != "12345":
            raise ValueError("invalid code")
        if self.require_password:
            raise TelegramSessionPasswordNeeded("password required")
        return object()

    async def complete_login_password(self, *, password: str):
        self.password_calls += 1
        if password != "correct":
            raise ValueError("invalid password")
        return object()

    async def disconnect(self) -> None:
        self.disconnected = True


@pytest.mark.asyncio
async def test_runtime_session_enrollment_authorizes_and_starts_ingestion():
    session = FakeSession()
    starts: list[str] = []

    async def on_authorized() -> None:
        starts.append("started")

    service = SessionEnrollmentServiceV3(
        session=session,
        connected=lambda: False,
        on_authorized=on_authorized,
    )

    assert await service.begin("+967700000000") == "code_requested"
    assert service.pending is True
    assert await service.submit_code("12345") == "authorized"
    assert service.pending is False
    assert starts == ["started"]


@pytest.mark.asyncio
async def test_runtime_session_enrollment_supports_two_step_password():
    session = FakeSession(require_password=True)
    starts: list[str] = []

    async def on_authorized() -> None:
        starts.append("started")

    service = SessionEnrollmentServiceV3(
        session=session,
        connected=lambda: False,
        on_authorized=on_authorized,
    )

    await service.begin("+967700000000")
    assert await service.submit_code("12345") == "password_required"
    assert service.awaiting_password is True
    assert await service.submit_password("correct") == "authorized"
    assert starts == ["started"]


@pytest.mark.asyncio
async def test_runtime_session_enrollment_fails_closed_when_already_connected():
    service = SessionEnrollmentServiceV3(
        session=FakeSession(),
        connected=lambda: True,
        on_authorized=lambda: None,
    )

    with pytest.raises(SessionEnrollmentError, match="session_already_connected"):
        await service.begin("+967700000000")


@pytest.mark.asyncio
async def test_runtime_session_cancel_clears_pending_state():
    session = FakeSession()

    async def on_authorized() -> None:
        return None

    service = SessionEnrollmentServiceV3(
        session=session,
        connected=lambda: False,
        on_authorized=on_authorized,
    )
    await service.begin("+967700000000")
    await service.cancel()

    assert service.pending is False
    assert session.disconnected is True
