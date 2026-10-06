from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.database import Database
from app.repositories.sources import SourceRepository
from app.telegram.listener import SourceListener
from app.telegram.session import TelegramSession


class FakeTelegramClient:
    def __init__(self, latest_by_chat: dict[int, int]):
        self.latest_by_chat = latest_by_chat
        self.handlers = []
        self.entity_requests: list[int] = []

    def add_event_handler(self, handler, event_builder):
        self.handlers.append((handler, event_builder))

    def remove_event_handler(self, handler):
        self.handlers = [(current, builder) for current, builder in self.handlers if current != handler]

    async def get_entity(self, chat_id):
        self.entity_requests.append(chat_id)
        return chat_id

    async def get_messages(self, entity, limit=1):
        assert limit == 1
        return [SimpleNamespace(id=self.latest_by_chat[entity])]


def test_listener_skips_history_and_rebaselines_after_restart(tmp_path):
    async def scenario():
        database = Database(tmp_path / "relay.sqlite3")
        await database.initialize()
        sources = SourceRepository(database)
        await sources.upsert(
            chat_id=-1001,
            input_ref="@source",
            title="Source",
            username="source",
            baseline_message_id=4,
        )
        client = FakeTelegramClient({-1001: 10})
        received: list[int] = []

        async def on_message(_source, messages):
            received.extend(message.id for message in messages)

        listener = SourceListener(client, sources, on_message)
        await listener.start()
        assert (await sources.get_by_chat_id(-1001)).baseline_message_id == 10

        await listener._handle_event(SimpleNamespace(chat_id=-1001, message=SimpleNamespace(id=9)))
        await listener._handle_event(SimpleNamespace(chat_id=-1001, message=SimpleNamespace(id=11)))
        assert received == [11]
        assert (await sources.get_by_chat_id(-1001)).baseline_message_id == 11

        client.latest_by_chat[-1001] = 20
        await listener.rebaseline()
        await listener._handle_event(SimpleNamespace(chat_id=-1001, message=SimpleNamespace(id=12)))
        await listener._handle_event(SimpleNamespace(chat_id=-1001, message=SimpleNamespace(id=21)))
        assert received == [11, 21]
        await listener.stop()
        assert listener.is_running is False

    asyncio.run(scenario())



def test_listener_registers_each_enabled_source_independently(tmp_path):
    async def scenario():
        database = Database(tmp_path / "relay.sqlite3")
        await database.initialize()
        sources = SourceRepository(database)
        for chat_id, latest in ((-1001, 10), (-1002, 20)):
            await sources.upsert(
                chat_id=chat_id,
                input_ref=f"@source{abs(chat_id)}",
                title=f"Source {chat_id}",
                username=None,
                baseline_message_id=latest,
            )
        client = FakeTelegramClient({-1001: 10, -1002: 20})
        received: list[tuple[int, int]] = []

        async def on_message(source, messages):
            received.extend((source.chat_id, message.id) for message in messages)

        listener = SourceListener(client, sources, on_message)
        await listener.start()

        assert len(client.handlers) == 2
        await listener._handle_event(
            SimpleNamespace(chat_id=-1001, message=SimpleNamespace(id=11)),
            expected_chat_id=-1001,
        )
        await listener._handle_event(
            SimpleNamespace(chat_id=-1002, message=SimpleNamespace(id=21)),
            expected_chat_id=-1002,
        )
        assert received == [(-1001, 11), (-1002, 21)]
        await listener.stop()
        assert client.handlers == []

    asyncio.run(scenario())

def test_listener_ignores_disabled_sources(tmp_path):
    async def scenario():
        database = Database(tmp_path / "relay.sqlite3")
        await database.initialize()
        sources = SourceRepository(database)
        await sources.upsert(
            chat_id=-1001,
            input_ref="@source",
            title="Source",
            username="source",
            baseline_message_id=1,
        )
        await sources.set_enabled(-1001, False)
        client = FakeTelegramClient({-1001: 1})
        received = []

        async def on_message(_source, messages):
            received.extend(message.id for message in messages)

        listener = SourceListener(client, sources, on_message)
        await listener.start()
        await listener._handle_event(SimpleNamespace(chat_id=-1001, message=SimpleNamespace(id=2)))
        assert received == []

    asyncio.run(scenario())


def test_telegram_session_constructs_without_network(tmp_path):
    session = TelegramSession(api_id=1, api_hash="hash", session_path=tmp_path / "user.session")
    assert session.client.api_id == 1
    asyncio.run(session.disconnect())
