from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.db import Destination, Source, SourceBinding, SourceCheckpoint, SourceRoute
from app.db.models import DestinationStatus, RouteStatus, SourceStatus
from app.telegram.v2_listener import V2TelegramListener


class FakeClient:
    def __init__(self):
        self.handlers = []
        self.latest = {"-1001": 10}
        self.latest_calls = []

    def is_connected(self):
        return True

    async def get_entity(self, chat_id):
        return str(chat_id)

    async def get_messages(self, entity, limit=1):
        self.latest_calls.append((entity, limit))
        return [SimpleNamespace(id=10)]

    def add_event_handler(self, handler, event_builder):
        self.handlers.append((handler, event_builder))

    def remove_event_handler(self, handler):
        self.handlers = [
            (current, builder) for current, builder in self.handlers if current != handler
        ]


class FakeManager:
    def __init__(self, client):
        self.client = client

    @property
    def is_connected(self):
        return True

    async def ensure_connected(self):
        return self.client


class FakeSession:
    def __init__(self, checkpoints):
        self.checkpoints = checkpoints
        self.added = []

    async def get(self, model, key):
        return self.checkpoints.get(key)

    def add(self, value):
        self.added.append(value)
        self.checkpoints[value.source_id] = value

    async def flush(self):
        return None

    async def rollback(self):
        return None

    def begin(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return None


class FakeSessionFactory:
    def __init__(self, checkpoints):
        self.checkpoints = checkpoints

    def __call__(self):
        session = FakeSession(self.checkpoints)
        return session


def make_binding():
    source_id = uuid.uuid4()
    route_id = uuid.uuid4()
    destination_id = uuid.uuid4()
    source = Source(
        id=source_id,
        account_id=uuid.uuid4(),
        telegram_account_id=uuid.uuid4(),
        telegram_chat_id=-1001,
        title="Source",
        status=SourceStatus.ACTIVE,
    )
    route = SourceRoute(
        id=route_id,
        account_id=source.account_id,
        source_id=source_id,
        destination_id=destination_id,
        status=RouteStatus.ACTIVE,
    )
    destination = Destination(
        id=destination_id,
        account_id=source.account_id,
        project_id=uuid.uuid4(),
        name="Target",
        telegram_chat_id=-1002,
        status=DestinationStatus.ACTIVE,
    )
    return SourceBinding(route=route, source=source, destination=destination)


def test_listener_delivers_one_event_per_active_route_and_advances_cursor():
    async def scenario():
        binding = make_binding()
        second = SourceRoute(
            id=uuid.uuid4(),
            account_id=binding.source.account_id,
            source_id=binding.source.id,
            destination_id=uuid.uuid4(),
            status=RouteStatus.ACTIVE,
        )
        second_destination = Destination(
            id=second.destination_id,
            account_id=binding.source.account_id,
            project_id=uuid.uuid4(),
            name="Second",
            telegram_chat_id=-1003,
            status=DestinationStatus.ACTIVE,
        )
        second_binding = SourceBinding(
            route=second,
            source=binding.source,
            destination=second_destination,
        )
        checkpoints = {
            binding.source.id: SourceCheckpoint(
                source_id=binding.source.id,
                last_seen_message_id=10,
            )
        }
        received = []

        async def on_message(event):
            received.append(event)

        listener = V2TelegramListener(
            FakeManager(FakeClient()),
            FakeSessionFactory(checkpoints),
            binding.source.account_id,
            on_message,
        )
        listener._bindings = {-1001: (binding, second_binding)}
        listener._accept_events = True

        await listener.handle_event(
            SimpleNamespace(chat_id=-1001, message=SimpleNamespace(id=11, grouped_id=77))
        )
        await listener.handle_event(SimpleNamespace(chat_id=-1001, message=SimpleNamespace(id=10)))

        assert len(received) == 2
        assert {event.route_id for event in received} == {binding.route.id, second.id}
        assert all(event.grouped_id == 77 for event in received)
        assert checkpoints[binding.source.id].last_seen_message_id == 11

    asyncio.run(scenario())


def test_listener_ignores_events_before_start_or_from_wrong_chat():
    async def scenario():
        binding = make_binding()
        received = []
        listener = V2TelegramListener(
            FakeManager(FakeClient()),
            FakeSessionFactory({}),
            binding.source.account_id,
            received.append,
        )
        listener._bindings = {-1001: (binding,)}

        await listener.handle_event(SimpleNamespace(chat_id=-1001, message=SimpleNamespace(id=12)))
        listener._accept_events = True
        await listener.handle_event(
            SimpleNamespace(chat_id=-1002, message=SimpleNamespace(id=12)),
            expected_chat_id=-1001,
        )
        assert received == []

    asyncio.run(scenario())


def test_listener_latest_message_query_is_limited_to_one():
    async def scenario():
        client = FakeClient()
        listener = V2TelegramListener(
            FakeManager(client),
            FakeSessionFactory({}),
            uuid.uuid4(),
            AsyncMock(),
        )
        assert await listener._latest_message_id(client, -1001) == 10
        assert client.latest_calls == [("-1001", 1)]

    asyncio.run(scenario())
