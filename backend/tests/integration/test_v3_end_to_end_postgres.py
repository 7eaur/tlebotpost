from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import delete, select

from app.db import Database
from app.db.models import (
    Account,
    Destination,
    DestinationStatus,
    JobStatus,
    Project,
    PublishedMessage,
    PublishJob,
    RouteExecution,
    RouteExecutionStatus,
    RouteStatus,
    Source,
    SourceCheckpoint,
    SourceRoute,
    SourceStatus,
    TelegramAccount,
)
from app.v3.content import ContentProcessingCoordinator
from app.v3.deduplication import DeduplicationCoordinator
from app.v3.publish_queue import PublishQueueV3
from app.v3.publisher import MediaStagerV3, PublisherWorkerComponent, TelegramPublisherV3
from app.v3.telegram import TelegramIngestionComponent

pytestmark = pytest.mark.integration


class FakeTelegramAdapter:
    def __init__(self) -> None:
        self.connected = False
        self.latest: dict[int, int] = {}
        self.callbacks: dict[int, Any] = {}
        self._disconnected: asyncio.Future[None] | None = None
        self.downloaded: list[tuple[int, int]] = []

    @property
    def is_connected(self) -> bool:
        return self.connected

    async def connect(self) -> None:
        self.connected = True
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

    async def download_message_media(
        self,
        chat_id: int,
        message_id: int,
        destination_dir: str | Path,
    ) -> Path:
        directory = Path(destination_dir)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{message_id}.bin"
        path.write_bytes(f"media-{message_id}".encode())
        self.downloaded.append((chat_id, message_id))
        return path

    async def emit(
        self,
        chat_id: int,
        message_id: int,
        *,
        text: str = "",
        grouped_id: int | None = None,
        media: Any = None,
    ) -> None:
        callback = self.callbacks.get(chat_id)
        if callback is None:
            return
        message = SimpleNamespace(
            id=message_id,
            chat_id=chat_id,
            grouped_id=grouped_id,
            date=datetime.now(UTC),
            raw_text=text,
            media=media,
        )
        await callback(SimpleNamespace(chat_id=chat_id, message=message))


class FakeBot:
    def __init__(self) -> None:
        self.next_id = 50000
        self.sent_text: list[tuple[int, str]] = []
        self.sent_media: list[tuple[int, str, str | None]] = []
        self.sent_albums: list[tuple[int, tuple[str, ...], str | None]] = []

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    def _id(self) -> int:
        self.next_id += 1
        return self.next_id

    async def send_text(self, chat_id: int, text: str) -> int:
        self.sent_text.append((chat_id, text))
        return self._id()

    async def send_media(self, chat_id: int, media, *, caption: str | None = None) -> int:
        self.sent_media.append((chat_id, media.media_type, caption))
        return self._id()

    async def send_album(self, chat_id: int, media, *, caption: str | None = None):
        self.sent_albums.append(
            (chat_id, tuple(item.media_type for item in media), caption)
        )
        return tuple(self._id() for _item in media)


async def _seed(session):
    account = Account(name="V3 E2E", slug=f"e2e-{uuid.uuid4().hex[:12]}")
    session.add(account)
    await session.flush()

    project = Project(
        account_id=account.id,
        name="E2E Project",
        slug=f"e2e-project-{uuid.uuid4().hex[:8]}",
    )
    telegram = TelegramAccount(
        account_id=account.id,
        label=f"telegram-{uuid.uuid4().hex[:8]}",
        session_key=f"session-{uuid.uuid4()}",
    )
    session.add_all([project, telegram])
    await session.flush()

    source = Source(
        account_id=account.id,
        telegram_account_id=telegram.id,
        telegram_chat_id=-1007300000001,
        title="E2E Source",
        status=SourceStatus.ACTIVE,
    )
    session.add(source)
    await session.flush()

    destinations = []
    for index in (1, 2):
        destination = Destination(
            account_id=account.id,
            project_id=project.id,
            name=f"E2E Target {index}",
            telegram_chat_id=-1007400000000 - index,
            status=DestinationStatus.ACTIVE,
        )
        session.add(destination)
        await session.flush()
        session.add(
            SourceRoute(
                account_id=account.id,
                source_id=source.id,
                destination_id=destination.id,
                status=RouteStatus.ACTIVE,
            )
        )
        destinations.append(destination)
    await session.flush()
    return account, telegram, source, tuple(destinations)


def _pipeline(database, account_id, adapter, tmp_path):
    queue = PublishQueueV3(
        database.session_factory,
        account_id,
        default_lease_seconds=30,
        retry_base_seconds=1,
        retry_cap_seconds=5,
    )
    dedup = DeduplicationCoordinator(
        database.session_factory,
        account_id,
        on_ready=queue.enqueue_result,
    )
    processor = ContentProcessingCoordinator(
        database.session_factory,
        account_id,
        on_ready=dedup.process,
    )
    bot = FakeBot()
    stager = MediaStagerV3(adapter, tmp_path / "stage", stale_after_seconds=60)
    publisher = TelegramPublisherV3(
        session_factory=database.session_factory,
        account_id=account_id,
        queue=queue,
        bot=bot,
        stager=stager,
    )
    worker = PublisherWorkerComponent(
        queue=queue,
        publisher=publisher,
        bot=bot,
        stager=stager,
        worker_id="e2e-publisher",
        poll_interval_seconds=0.01,
        batch_size=20,
        lease_seconds=30,
    )
    return processor, bot, worker


async def _flush(component: TelegramIngestionComponent) -> None:
    await asyncio.sleep(0.02)
    await component.flush_pending()


@pytest.mark.asyncio
async def test_v3_full_pipeline_fanout_dedup_album_and_restart_live_only(tmp_path):
    if not (os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")):
        pytest.skip("PostgreSQL test URL is required")

    database = Database.from_env()
    adapter = FakeTelegramAdapter()
    account_id = None
    ingestion = None
    try:
        async with database.transaction() as session:
            account, telegram, source, destinations = await _seed(session)
            account_id = account.id
            telegram_account_id = telegram.id
            source_id = source.id
            chat_id = source.telegram_chat_id
            target_ids = {item.telegram_chat_id for item in destinations}

        processor, bot, worker = _pipeline(database, account_id, adapter, tmp_path)
        adapter.latest[chat_id] = 100

        ingestion = TelegramIngestionComponent(
            adapter=adapter,
            session_factory=database.session_factory,
            account_id=account_id,
            telegram_account_id=telegram_account_id,
            album_window_seconds=0.01,
            reconnect_delays=(0.01,),
            on_registration=processor.process_registration,
        )
        await ingestion.start()

        await adapter.emit(chat_id, 101, text="خبر فريد للاختبار")
        await _flush(ingestion)
        assert await worker.run_once() == 2
        assert {chat for chat, _text in bot.sent_text} == target_ids

        await adapter.emit(chat_id, 102, text="خبر فريد للاختبار")
        await _flush(ingestion)
        assert await worker.run_once() == 0

        photo = SimpleNamespace(media_type="photo", identity="photo:e2e-1")
        video = SimpleNamespace(media_type="video", identity="video:e2e-2")
        await adapter.emit(
            chat_id,
            103,
            text="ألبوم تجريبي",
            grouped_id=9001,
            media=photo,
        )
        await adapter.emit(
            chat_id,
            104,
            grouped_id=9001,
            media=video,
        )
        await _flush(ingestion)
        assert await worker.run_once() == 2
        assert len(bot.sent_albums) == 2
        assert {chat for chat, _types, _caption in bot.sent_albums} == target_ids
        assert all(types == ("photo", "video") for _chat, types, _caption in bot.sent_albums)
        assert sorted(message_id for _chat, message_id in adapter.downloaded) == [
            103,
            103,
            104,
            104,
        ]

        async with database.session() as session:
            executions = list(
                (
                    await session.scalars(
                        select(RouteExecution)
                        .where(RouteExecution.account_id == account_id)
                        .order_by(RouteExecution.cursor_message_id, RouteExecution.destination_id)
                    )
                ).all()
            )
            jobs = list(
                (
                    await session.scalars(
                        select(PublishJob).where(PublishJob.account_id == account_id)
                    )
                ).all()
            )
            published = list(
                (
                    await session.scalars(
                        select(PublishedMessage)
                        .join(PublishJob, PublishJob.id == PublishedMessage.publish_job_id)
                        .where(PublishJob.account_id == account_id)
                    )
                ).all()
            )
            checkpoint = await session.get(SourceCheckpoint, source_id)

        by_cursor: dict[int, list[RouteExecution]] = {}
        for execution in executions:
            by_cursor.setdefault(execution.cursor_message_id, []).append(execution)

        assert {item.status for item in by_cursor[101]} == {RouteExecutionStatus.PUBLISHED}
        assert {item.status for item in by_cursor[102]} == {RouteExecutionStatus.DUPLICATE}
        assert {item.status for item in by_cursor[104]} == {RouteExecutionStatus.PUBLISHED}
        assert len(jobs) == 4
        assert all(job.status is JobStatus.PUBLISHED for job in jobs)
        assert len(published) == 4
        assert checkpoint is not None
        assert checkpoint.last_seen_message_id == 104
        assert checkpoint.last_committed_message_id == 104

        await ingestion.stop()
        ingestion = None

        adapter.latest[chat_id] = 110
        restarted = TelegramIngestionComponent(
            adapter=adapter,
            session_factory=database.session_factory,
            account_id=account_id,
            telegram_account_id=telegram_account_id,
            album_window_seconds=0.01,
            reconnect_delays=(0.01,),
            on_registration=processor.process_registration,
        )
        ingestion = restarted
        await restarted.start()

        await adapter.emit(chat_id, 109, text="قديم يجب تجاهله")
        await adapter.emit(chat_id, 111, text="جديد بعد إعادة التشغيل")
        await _flush(restarted)
        assert await worker.run_once() == 2

        async with database.session() as session:
            keys = set(
                (
                    await session.scalars(
                        select(RouteExecution.event_key).where(
                            RouteExecution.account_id == account_id
                        )
                    )
                ).all()
            )
            checkpoint = await session.get(SourceCheckpoint, source_id)
        assert "message:109" not in keys
        assert "message:111" in keys
        assert checkpoint is not None
        assert checkpoint.last_seen_message_id == 111
        assert checkpoint.last_committed_message_id == 111
    finally:
        if ingestion is not None:
            await ingestion.stop()
        if account_id is not None:
            async with database.transaction() as session:
                await session.execute(delete(Account).where(Account.id == account_id))
        await database.close()
