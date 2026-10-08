from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.v3.control import ControlBotV3, ControlStatus


class FakeMessage:
    def __init__(self) -> None:
        self.replies: list[str] = []

    async def reply_text(self, text: str) -> None:
        self.replies.append(text)


class FakeService:
    def __init__(self) -> None:
        self.status_calls = 0

    async def status(self) -> ControlStatus:
        self.status_calls += 1
        return ControlStatus(
            telegram_status="active",
            telegram_connected=True,
            telegram_last_error=None,
            projects_total=1,
            projects_active=1,
            sources_total=2,
            sources_active=2,
            destinations_total=1,
            destinations_active=1,
            routes_total=2,
            routes_active=2,
            queue_pending=0,
            queue_failed=0,
            last_publish_error=None,
        )


def update(user_id: int, *, chat_type: str = "private"):
    message = FakeMessage()
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        effective_chat=SimpleNamespace(type=chat_type),
        effective_message=message,
    )


@pytest.mark.asyncio
async def test_control_bot_denies_non_owner_before_status_service_call():
    service = FakeService()
    bot = ControlBotV3(token="test-token", owner_id=100, service=service)
    request = update(200)

    await bot.status_command(request, SimpleNamespace(args=[]))

    assert service.status_calls == 0
    assert request.effective_message.replies == ["غير مصرح."]


@pytest.mark.asyncio
async def test_control_bot_denies_owner_outside_private_chat():
    service = FakeService()
    bot = ControlBotV3(token="test-token", owner_id=100, service=service)
    request = update(100, chat_type="group")

    await bot.status_command(request, SimpleNamespace(args=[]))

    assert service.status_calls == 0
    assert request.effective_message.replies == ["غير مصرح."]


@pytest.mark.asyncio
async def test_control_bot_owner_can_read_status_without_message_content():
    service = FakeService()
    bot = ControlBotV3(token="test-token", owner_id=100, service=service)
    request = update(100)

    await bot.status_command(request, SimpleNamespace(args=[]))

    assert service.status_calls == 1
    reply = request.effective_message.replies[-1]
    assert "Sources: 2/2" in reply
    assert "Queue failed: 0" in reply
    assert "لا يوجد" in reply


def test_control_bot_builds_without_network_calls():
    bot = ControlBotV3(token="123:TEST", owner_id=100, service=FakeService())
    application = bot.build_application()

    assert application is bot.application
    assert application.handlers
