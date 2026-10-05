from __future__ import annotations

import asyncio
from types import SimpleNamespace

import app.relay.publisher as publisher_module
from app.control.runtime import RelayRuntime
from app.database import Database
from app.relay.publisher import Publisher
from app.repositories.events import EventLogRepository
from app.repositories.settings import SettingsRepository
from app.repositories.sources import SourceRepository
from app.telegram.listener import SourceListener


class FakeClient:
    def __init__(self, latest=0):
        self.latest = latest
        self.handler = None

    def add_event_handler(self, handler, _builder):
        self.handler = handler

    def remove_event_handler(self, _handler):
        self.handler = None

    async def get_entity(self, chat_id):
        return chat_id

    async def get_messages(self, _entity, limit=1):
        return [SimpleNamespace(id=self.latest)] if limit == 1 else []


def test_listener_does_not_advance_cursor_when_album_publish_fails(tmp_path):
    async def scenario():
        database = Database(tmp_path / "relay.sqlite3")
        await database.initialize()
        sources = SourceRepository(database)
        await sources.upsert(
            chat_id=-1001,
            input_ref="@source",
            title="Source",
            username="source",
            baseline_message_id=0,
        )
        client = FakeClient(latest=0)

        async def failing_publisher(_source, _messages):
            raise RuntimeError("send failed")

        listener = SourceListener(client, sources, failing_publisher, album_window_seconds=0.01)
        await listener.start()
        events = [
            SimpleNamespace(chat_id=-1001, message=SimpleNamespace(id=1, grouped_id=4)),
            SimpleNamespace(chat_id=-1001, message=SimpleNamespace(id=2, grouped_id=4)),
        ]
        results = await asyncio.gather(
            *(listener._handle_event(event) for event in events), return_exceptions=True
        )
        assert all(isinstance(result, RuntimeError) for result in results)
        assert (await sources.get_by_chat_id(-1001)).baseline_message_id == 0
        await listener.stop()

    asyncio.run(scenario())


def test_runtime_reconnects_and_rebaselines():
    async def scenario():
        class Session:
            def __init__(self):
                self.client = SimpleNamespace(connected=False)
                self.connects = 0
                self.disconnects = 0

            async def connect(self):
                self.connects += 1
                self.client.connected = True

            async def disconnect(self):
                self.disconnects += 1
                self.client.connected = False

        class Listener:
            def __init__(self):
                self.starts = 0
                self.rebaselines = 0
                self.stops = 0

            async def start(self):
                self.starts += 1

            async def rebaseline(self):
                self.rebaselines += 1

            async def stop(self):
                self.stops += 1

        session = Session()
        listener = Listener()
        runtime = RelayRuntime(session, listener, reconnect_delays=(0.001,))
        await runtime.start()
        session.client.connected = False
        await runtime._recover_connection()
        assert session.connects == 2
        assert listener.rebaselines == 1
        await runtime.stop()
        assert session.disconnects == 1

    asyncio.run(scenario())


def test_publisher_retries_flood_wait(monkeypatch, tmp_path):
    async def scenario():
        class FakeRetryAfter(Exception):
            retry_after = 0

        monkeypatch.setattr(publisher_module, "RetryAfter", FakeRetryAfter)
        database = Database(tmp_path / "relay.sqlite3")
        await database.initialize()
        publisher = Publisher(
            SimpleNamespace(),
            SimpleNamespace(),
            SettingsRepository(database),
            EventLogRepository(database),
            retry_after_retries=1,
            send_interval_seconds=0,
        )
        calls = 0

        async def send():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise FakeRetryAfter()
            return "ok"

        assert await publisher._send_with_retry(send) == "ok"
        assert calls == 2

    asyncio.run(scenario())
