from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.control.bot import ControlBot
from app.control.resolver import normalize_chat_reference
from app.database import Database
from app.repositories.settings import SettingsRepository
from app.repositories.sources import SourceRepository
from app.telegram.session import normalize_code, normalize_phone


class FakeResolver:
    async def resolve(self, input_ref):
        from app.control.resolver import ResolvedChat

        return ResolvedChat(-1001, input_ref, "Channel", "channel", 12)


class FakeSession:
    def __init__(self):
        self.client = SimpleNamespace(is_connected=lambda: True)
        self.phone = None
        self.completed = None

    async def request_login_code(self, phone):
        self.phone = phone
        return SimpleNamespace(phone_code_hash="hash")

    async def complete_login(self, **kwargs):
        self.completed = kwargs


class FakeRuntime:
    is_running = False

    async def start(self):
        self.is_running = True

    async def stop(self):
        self.is_running = False


class FakeMessage:
    def __init__(self, text=""):
        self.text = text
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)


def make_update(text=""):
    message = FakeMessage(text)
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=42),
        effective_message=message,
        message=message,
    )


def test_references_and_phone_values_are_normalized():
    assert normalize_chat_reference("https://t.me/example/123?single") == "@example"
    assert normalize_chat_reference("t.me/c/123456/99") == "-100123456"
    assert normalize_chat_reference("https://t.me/+invitehash") == "invite:invitehash"
    assert normalize_phone("00967 700-000-000") == "+967700000000"
    assert normalize_phone("+967٧٠٠٠٠٠٠٠٠") == "+967700000000"
    assert normalize_code("١٢٣ ٤٥") == "12345"


def test_guided_source_target_and_login_flow(tmp_path):
    async def scenario():
        database = Database(tmp_path / "relay.sqlite3")
        await database.initialize()
        session = FakeSession()
        bot = ControlBot(
            token="123:token",
            owner_id=42,
            sources=SourceRepository(database),
            settings=SettingsRepository(database),
            resolver=FakeResolver(),
            runtime=FakeRuntime(),
            session=session,
        )

        await bot.add_source(make_update(), SimpleNamespace(args=[]))
        await bot.menu_message(make_update("https://t.me/source"), SimpleNamespace(args=[]))
        assert len(await bot.sources.list()) == 1

        await bot.set_target(make_update(), SimpleNamespace(args=[]))
        await bot.menu_message(make_update("https://t.me/target"), SimpleNamespace(args=[]))
        assert (await bot.settings.get()).target_chat_id == -1001

        await bot.login(make_update(), SimpleNamespace(args=[]))
        await bot.menu_message(make_update("+967700000000"), SimpleNamespace(args=[]))
        await bot.menu_message(make_update("١٢٣٤٥ password"), SimpleNamespace(args=[]))
        assert session.phone == "+967700000000"
        assert session.completed["code"] == "١٢٣٤٥"
        assert session.completed["password"] == "password"
        assert bot._pending_action is None

    asyncio.run(scenario())
