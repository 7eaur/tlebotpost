from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.control.bot import ControlBot
from app.control.resolver import ResolvedChat
from app.database import Database
from app.repositories.settings import SettingsRepository
from app.repositories.sources import SourceRepository
from app.telegram.session import TelegramSessionPasswordNeeded


class FakeResolver:
    async def resolve(self, input_ref):
        return ResolvedChat(-1001, input_ref, "Source", "source", 25)


class FakeRuntime:
    def __init__(self):
        self.is_running = False
        self.starts = 0
        self.stops = 0

    async def start(self):
        self.starts += 1
        self.is_running = True

    async def stop(self):
        self.stops += 1
        self.is_running = False


class FakeSession:
    def __init__(self):
        self.phone = None
        self.completed = None

    async def request_login_code(self, phone):
        self.phone = phone
        return SimpleNamespace(phone_code_hash="hash")

    async def complete_login(self, **kwargs):
        self.completed = kwargs

    async def complete_login_password(self, *, password):
        self.password = password


class FakeMessage:
    def __init__(self, text=""):
        self.text = text
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)


def update(user_id: int, text=""):
    message = FakeMessage(text)
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        effective_message=message,
        message=message,
    )


def test_control_bot_manages_configuration_and_runtime(tmp_path):
    async def scenario():
        database = Database(tmp_path / "relay.sqlite3")
        await database.initialize()
        sources = SourceRepository(database)
        settings = SettingsRepository(database)
        runtime = FakeRuntime()
        bot = ControlBot(
            token="123:token",
            owner_id=42,
            sources=sources,
            settings=settings,
            resolver=FakeResolver(),
            runtime=runtime,
        )

        add = update(42)
        await bot.add_source(add, SimpleNamespace(args=["@source"]))
        assert (await sources.list())[0].baseline_message_id == 25

        target = update(42)
        await bot.set_target(target, SimpleNamespace(args=["@target"]))
        assert (await settings.get()).target_chat_id == -1001

        include = update(42)
        await bot.set_include(include, SimpleNamespace(args=["خبر,تقنية"]))
        assert (await settings.get()).include_keywords == ("خبر", "تقنية")

        start = update(42)
        await bot.start_relay(start, SimpleNamespace(args=[]))
        assert runtime.starts == 1
        assert (await settings.get()).enabled is True

        stop = update(42)
        await bot.stop_relay(stop, SimpleNamespace(args=[]))
        assert runtime.stops == 1
        assert (await settings.get()).enabled is False

    asyncio.run(scenario())


def test_control_bot_rejects_non_owner(tmp_path):
    async def scenario():
        database = Database(tmp_path / "relay.sqlite3")
        await database.initialize()
        sources = SourceRepository(database)
        bot = ControlBot(
            token="123:token",
            owner_id=42,
            sources=sources,
            settings=SettingsRepository(database),
            resolver=FakeResolver(),
            runtime=FakeRuntime(),
        )
        unauthorized = update(99)
        await bot.add_source(unauthorized, SimpleNamespace(args=["@source"]))
        assert await sources.list() == []
        assert unauthorized.effective_message.replies == [
            "غير مصرح لك باستخدام هذا البوت."
        ]

    asyncio.run(scenario())


def test_control_bot_builds_handlers_without_network():
    bot = ControlBot(
        token="123:token",
        owner_id=42,
        sources=None,
        settings=None,
        resolver=None,
        runtime=None,
    )
    application = bot.build_application()
    assert application is bot.application


def test_control_bot_login_flow_keeps_pending_state_in_memory(tmp_path):
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
        request = update(42)
        await bot.login(request, SimpleNamespace(args=["+967700000000"]))
        assert session.phone == "+967700000000"
        complete = update(42)
        await bot.login_code(complete, SimpleNamespace(args=["12345", "password"]))
        assert session.completed["code"] == "12345"
        assert bot._login_phone is None

    asyncio.run(scenario())


def test_control_bot_requests_two_step_password_as_follow_up(tmp_path):
    async def scenario():
        database = Database(tmp_path / "relay.sqlite3")
        await database.initialize()
        session = FakeSession()

        async def requires_password(**kwargs):
            session.completed = kwargs
            raise TelegramSessionPasswordNeeded("password required")

        session.complete_login = requires_password
        bot = ControlBot(
            token="123:token",
            owner_id=42,
            sources=SourceRepository(database),
            settings=SettingsRepository(database),
            resolver=FakeResolver(),
            runtime=FakeRuntime(),
            session=session,
        )

        await bot.login(update(42), SimpleNamespace(args=["+967700000000"]))
        await bot.login_code(update(42), SimpleNamespace(args=["12345"]))
        assert bot._pending_action == "login_password"

        await bot.menu_message(update(42, "secret-password"), SimpleNamespace(args=[]))
        assert session.password == "secret-password"
        assert bot._pending_action is None

    asyncio.run(scenario())
