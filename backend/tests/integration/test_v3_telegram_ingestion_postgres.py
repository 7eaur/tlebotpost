from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import delete, select

from app.db import Database
from app.db.models import (
    Account,
    Destination,
    DestinationStatus,
    Project,
    ProjectStatus,
    RouteExecution,
    RouteExecutionStatus,
    RouteStatus,
    Source,
    SourceCheckpoint,
    SourceRoute,
    SourceStatus,
    TelegramAccount,
    TelegramAccountStatus,
)
from app.v3.telegram import SourceEvent, TelegramIngestionComponent

pytestmark = pytest.mark.integration


class FakeTelegramAdapter:
    def __init__(self) -> None:
        self.connected = False
        self.latest: dict[int, int] = {}
        self.callbacks: dict[int, Any] = {}
        self.connect_count = 0
        self._disconnected: asyncio.Future[None] | None = None

    @property
    def is_connected(self) -> bool:
        return self.connected

    async def connect(self) -> None:
        self.connected = True
        self.connect_count += 1
        self._disconnected = asyncio.get_running_loop().create_future()

    async def disconnect(self) -> None:
        self.connected = False
        if self._disconnected is not None and not self._disconnected.done():
            self._disconnected.set_result(None)

    async def latest_message_id(self, chat_id: int) -> int:
        return self.latest.get(chat_id, 0)

    async def subscribe(self, chat_id: int, callback):
        self.callbacks[chat_id] = callback
        return chat_id

    async def unsubscribe(self, token: Any) -> None:
        self.callbacks.pop(int(token), None)

    async def wait_disconnected(self) -> None:
        assert self._disconnected is not None
        await self._disconnected

    async def emit(
        self,
        chat_id: int,
        message_id: int,
        *,
        grouped_id: int | None = None,
    ) -> None:
        callback = self.callbacks.get(chat_id)
        if callback is None:
            return
        raw = SimpleNamespace(
            id=message_id,
            chat_id=chat_id,
            grouped_id=grouped_id,
            date=datetime.now(UTC),
        )
        await callback(SimpleNamespace(chat_id=chat_id, message=raw))

    def lose_connection(self) -> None:
        self.connected = False
        assert self._disconnected is not None
        if not self._disconnected.done():
            self._disconnected.set_result(None)


async def _seed(session):
    account = Account(name="Phase3", slug=f"p3-{uuid.uuid4().hex[:12]}")
    session.add(account)
    await session.flush()

    project = Project(
        account_id=account.id,
        name="P3 Project",
        slug=f"project-{uuid.uuid4().hex[:10]}",
        status=ProjectStatus.ACTIVE,
    )
    telegram = TelegramAccount(
        account_id=account.id,
        label=f"telegram-{uuid.uuid4().hex[:8]}",
        session_key=f"session-{uuid.uuid4()}",
        status=TelegramAccountStatus.ACTIVE,
    )
    session.add_all([project, telegram])
    await session.flush()

    source = Source(
        account_id=account.id,
        telegram_account_id=telegram.id,
        telegram_chat_id=-1003000000001,
        title="Source",
        status=SourceStatus.ACTIVE,
    )
    session.add(source)
    await session.flush()

    destination = Destination(
        account_id=account.id,
        project_id=project.id,
        name="Target",
        telegram_chat_id=-1004000000001,
        status=DestinationStatus.ACTIVE,
    )
    session.add(destination)
    await session.flush()

    route = SourceRoute(
        account_id=account.id,
        source_id=source.id,
        destination_id=destination.id,
        status=RouteStatus.ACTIVE,
    )
    session.add(route)
    await session.flush()
    return account, project, telegram, source, destination, route


async def _wait_until(predicate, *, attempts: int = 100) -> None:
    for _ in range(attempts):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition did not become true")


@pytest.mark.asyncio
async def test_v3_ingestion_live_baseline_album_and_reconnect():
    if not (os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")):
        pytest.skip("TEST_DATABASE_URL or DATABASE_URL is required")

    database = Database.from_env()
    adapter = FakeTelegramAdapter()
    account_id = None
    component = None
    registrations: list[SourceEvent] = []
    try:
        async with database.transaction() as session:
            account, _project, telegram, source, _destination, _route = await _seed(session)
            account_id = account.id
            source_id = source.id
            telegram_account_id = telegram.id
            chat_id = source.telegram_chat_id

        adapter.latest[chat_id] = 10

        async def on_registration(event, _registration) -> None:
            registrations.append(event)

        component = TelegramIngestionComponent(
            adapter=adapter,
            session_factory=database.session_factory,
            account_id=account_id,
            telegram_account_id=telegram_account_id,
            album_window_seconds=60,
            reconnect_delays=(0.01, 0.02),
            on_registration=on_registration,
        )
        await component.start()

        async with database.session() as session:
            checkpoint = await session.get(SourceCheckpoint, source_id)
            assert checkpoint is not None
            assert checkpoint.last_seen_message_id == 10
            assert checkpoint.last_committed_message_id == 0

        await adapter.emit(chat_id, 9)
        await adapter.emit(chat_id, 11)
        await adapter.emit(chat_id, 12, grouped_id=77)
        await adapter.emit(chat_id, 13, grouped_id=77)
        await component.flush_pending()

        assert [event.message_ids for event in registrations] == [(11,), (12, 13)]

        async with database.session() as session:
            executions = list(
                (
                    await session.scalars(
                        select(RouteExecution)
                        .where(RouteExecution.account_id == account_id)
                        .order_by(RouteExecution.cursor_message_id)
                    )
                ).all()
            )
            assert [(item.event_key, item.cursor_message_id) for item in executions] == [
                ("message:11", 11),
                ("group:77", 13),
            ]
            assert all(item.status is RouteExecutionStatus.RECEIVED for item in executions)
            checkpoint = await session.get(SourceCheckpoint, source_id)
            assert checkpoint is not None
            assert checkpoint.last_seen_message_id == 13
            assert checkpoint.last_committed_message_id == 0

        adapter.latest[chat_id] = 20
        adapter.lose_connection()
        await _wait_until(lambda: adapter.connect_count >= 2 and chat_id in adapter.callbacks)

        async with database.session() as session:
            checkpoint = await session.get(SourceCheckpoint, source_id)
            assert checkpoint is not None
            assert checkpoint.last_seen_message_id == 20
            assert checkpoint.last_committed_message_id == 0

        await adapter.emit(chat_id, 20)
        await adapter.emit(chat_id, 21)

        async with database.session() as session:
            keys = list(
                (
                    await session.scalars(
                        select(RouteExecution.event_key)
                        .where(RouteExecution.account_id == account_id)
                        .order_by(RouteExecution.cursor_message_id)
                    )
                ).all()
            )
            assert keys == ["message:11", "group:77", "message:21"]
            checkpoint = await session.get(SourceCheckpoint, source_id)
            assert checkpoint is not None
            assert checkpoint.last_seen_message_id == 21
            assert checkpoint.last_committed_message_id == 0
    finally:
        if component is not None:
            await component.stop()
        if account_id is not None:
            async with database.transaction() as session:
                await session.execute(delete(Account).where(Account.id == account_id))
        await database.close()


@pytest.mark.asyncio
async def test_v3_ingestion_reload_removes_paused_project_source():
    if not (os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")):
        pytest.skip("TEST_DATABASE_URL or DATABASE_URL is required")

    database = Database.from_env()
    adapter = FakeTelegramAdapter()
    account_id = None
    component = None
    try:
        async with database.transaction() as session:
            account, project, telegram, source, _destination, _route = await _seed(session)
            account_id = account.id
            project_id = project.id
            telegram_account_id = telegram.id
            chat_id = source.telegram_chat_id

        adapter.latest[chat_id] = 5
        component = TelegramIngestionComponent(
            adapter=adapter,
            session_factory=database.session_factory,
            account_id=account_id,
            telegram_account_id=telegram_account_id,
            album_window_seconds=0.01,
            reconnect_delays=(0.01,),
        )
        await component.start()
        assert chat_id in adapter.callbacks

        async with database.transaction() as session:
            project = await session.get(Project, project_id)
            assert project is not None
            project.status = ProjectStatus.PAUSED

        await component.reload(rebaseline=False)
        assert chat_id not in adapter.callbacks
        await adapter.emit(chat_id, 6)

        async with database.session() as session:
            count = len(
                (
                    await session.scalars(
                        select(RouteExecution).where(RouteExecution.account_id == account_id)
                    )
                ).all()
            )
            assert count == 0
    finally:
        if component is not None:
            await component.stop()
        if account_id is not None:
            async with database.transaction() as session:
                await session.execute(delete(Account).where(Account.id == account_id))
        await database.close()
