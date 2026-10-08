from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import delete, select
from telegram.error import BadRequest, RetryAfter

from app.db import Database
from app.db.models import (
    Account,
    AttemptStatus,
    ContentType,
    Destination,
    DestinationStatus,
    JobStatus,
    Project,
    PublicationAttempt,
    PublishedMessage,
    PublishingMode,
    PublishJob,
    RouteExecution,
    RouteExecutionStatus,
    RoutePublishPayload,
    RouteStatus,
    Source,
    SourceRoute,
    TelegramAccount,
)
from app.v3.publish_queue import PublishQueueV3
from app.v3.publisher import (
    MediaStagerV3,
    PublisherWorkerComponent,
    TelegramPublisherV3,
)

pytestmark = pytest.mark.integration


class FakeUserAdapter:
    def __init__(self) -> None:
        self.downloaded: list[tuple[int, int, Path]] = []

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
        self.downloaded.append((chat_id, message_id, path))
        return path


class FakeBot:
    def __init__(self, *, fail_on_call: int | None = None, failure: Exception | None = None):
        self.fail_on_call = fail_on_call
        self.failure = failure
        self.call_count = 0
        self.next_id = 9000
        self.sent_text: list[tuple[int, str]] = []
        self.sent_media: list[tuple[int, str, bytes, str | None]] = []
        self.sent_albums: list[tuple[int, list[str], str | None]] = []
        self.started = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.started = False

    def _before(self) -> None:
        self.call_count += 1
        if self.fail_on_call == self.call_count and self.failure is not None:
            raise self.failure

    def _id(self) -> int:
        self.next_id += 1
        return self.next_id

    async def send_text(self, chat_id: int, text: str) -> int:
        self._before()
        self.sent_text.append((chat_id, text))
        return self._id()

    async def send_media(self, chat_id: int, media, *, caption: str | None = None) -> int:
        self._before()
        self.sent_media.append(
            (chat_id, media.media_type, media.path.read_bytes(), caption)
        )
        return self._id()

    async def send_album(self, chat_id: int, media, *, caption: str | None = None):
        self._before()
        self.sent_albums.append(
            (chat_id, [item.media_type for item in media], caption)
        )
        return tuple(self._id() for _item in media)


async def seed_job(
    session,
    *,
    rendered_text: str,
    content_type: ContentType = ContentType.TEXT,
    media_json: list[dict] | None = None,
    max_attempts: int = 3,
):
    account = Account(name="V3 Publisher", slug=f"pub-{uuid.uuid4().hex[:10]}")
    session.add(account)
    await session.flush()

    project = Project(
        account_id=account.id,
        name="Publisher Project",
        slug=f"pub-project-{uuid.uuid4().hex[:8]}",
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
        telegram_chat_id=-1009100000001,
        title="Publisher Source",
    )
    destination = Destination(
        account_id=account.id,
        project_id=project.id,
        name="Publisher Destination",
        telegram_chat_id=-1009200000001,
        status=DestinationStatus.ACTIVE,
        publishing_mode=PublishingMode.DIRECT,
    )
    session.add_all([source, destination])
    await session.flush()

    route = SourceRoute(
        account_id=account.id,
        source_id=source.id,
        destination_id=destination.id,
        status=RouteStatus.ACTIVE,
        publishing_mode=PublishingMode.DIRECT,
    )
    session.add(route)
    await session.flush()

    message_id = 3000 + len(rendered_text) % 997
    execution = RouteExecution(
        account_id=account.id,
        source_id=source.id,
        route_id=route.id,
        destination_id=destination.id,
        event_key=f"message:{message_id}",
        cursor_message_id=message_id,
        telegram_message_id=message_id,
        status=RouteExecutionStatus.QUEUED,
        reason_code="queue_enqueued",
    )
    session.add(execution)
    await session.flush()

    payload = RoutePublishPayload(
        account_id=account.id,
        route_execution_id=execution.id,
        content_type=content_type,
        normalized_text=rendered_text or None,
        rendered_text=rendered_text or None,
        media_json=media_json or [],
    )
    session.add(payload)
    await session.flush()

    now = datetime(2026, 10, 9, 0, 0, tzinfo=UTC)
    job = PublishJob(
        account_id=account.id,
        content_item_id=None,
        route_execution_id=execution.id,
        route_payload_id=payload.id,
        destination_id=destination.id,
        source_route_id=route.id,
        status=JobStatus.QUEUED,
        scheduled_for=now,
        max_attempts=max_attempts,
    )
    session.add(job)
    await session.flush()
    execution.publish_job_id = job.id
    await session.flush()
    return account, source, destination, execution, job


def make_worker(database, account_id, tmp_path, bot, user):
    queue = PublishQueueV3(
        database.session_factory,
        account_id,
        default_lease_seconds=30,
        retry_base_seconds=5,
        retry_cap_seconds=30,
    )
    stager = MediaStagerV3(user, tmp_path / "stage", stale_after_seconds=60)
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
        worker_id="publisher-test",
        poll_interval_seconds=0.1,
        batch_size=10,
        lease_seconds=30,
    )
    return queue, stager, worker


@pytest.mark.asyncio
async def test_text_job_is_published_and_persisted(tmp_path):
    if not (os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")):
        pytest.skip("TEST_DATABASE_URL or DATABASE_URL is required")
    database = Database.from_env()
    account_id = None
    try:
        async with database.transaction() as session:
            account, _source, destination, execution, job = await seed_job(
                session,
                rendered_text="خبر منشور",
            )
            account_id = account.id
            job_id = job.id
            execution_id = execution.id

        bot = FakeBot()
        queue, _stager, worker = make_worker(
            database, account_id, tmp_path, bot, FakeUserAdapter()
        )
        assert await worker.run_once() == 1

        async with database.session() as session:
            stored_job = await session.get(PublishJob, job_id)
            stored_execution = await session.get(RouteExecution, execution_id)
            attempt = await session.scalar(
                select(PublicationAttempt).where(
                    PublicationAttempt.publish_job_id == job_id
                )
            )
            published = await session.scalar(
                select(PublishedMessage).where(PublishedMessage.publish_job_id == job_id)
            )
            assert stored_job is not None
            assert stored_job.status is JobStatus.PUBLISHED
            assert stored_execution is not None
            assert stored_execution.status is RouteExecutionStatus.PUBLISHED
            assert attempt is not None
            assert attempt.status is AttemptStatus.SUCCEEDED
            assert published is not None
            assert published.destination_id == destination.id
            assert published.metadata_json["message_count"] == 1
            assert published.metadata_json["partial"] is False
            assert bot.sent_text == [(destination.telegram_chat_id, "خبر منشور")]
            assert await queue.claim_due(worker_id="other") == []
    finally:
        if account_id is not None:
            async with database.transaction() as session:
                await session.execute(delete(Account).where(Account.id == account_id))
        await database.close()


@pytest.mark.asyncio
async def test_long_media_caption_falls_back_to_media_then_full_text_and_cleans_staging(tmp_path):
    if not (os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")):
        pytest.skip("TEST_DATABASE_URL or DATABASE_URL is required")
    database = Database.from_env()
    account_id = None
    try:
        text = "ن" * 1100
        async with database.transaction() as session:
            account, source, destination, _execution, job = await seed_job(
                session,
                rendered_text=text,
                content_type=ContentType.PHOTO,
                media_json=[
                    {
                        "media_type": "photo",
                        "identity": "photo:1",
                        "source_message_id": 3101,
                        "file_name": None,
                        "mime_type": "image/jpeg",
                        "byte_size": 10,
                    }
                ],
            )
            account_id = account.id
            job_id = job.id

        bot = FakeBot()
        user = FakeUserAdapter()
        _queue, stager, worker = make_worker(database, account_id, tmp_path, bot, user)
        assert await worker.run_once() == 1

        assert len(bot.sent_media) == 1
        assert bot.sent_media[0][0] == destination.telegram_chat_id
        assert bot.sent_media[0][3] is None
        assert bot.sent_text == [(destination.telegram_chat_id, text)]
        assert [(item[0], item[1]) for item in user.downloaded] == [
            (source.telegram_chat_id, 3101)
        ]
        assert not any(stager.root.glob("job-*"))

        async with database.session() as session:
            published = await session.scalar(
                select(PublishedMessage).where(PublishedMessage.publish_job_id == job_id)
            )
            assert published is not None
            assert published.metadata_json["message_count"] == 2
    finally:
        if account_id is not None:
            async with database.transaction() as session:
                await session.execute(delete(Account).where(Account.id == account_id))
        await database.close()


@pytest.mark.asyncio
async def test_album_is_one_job_and_one_bot_media_group(tmp_path):
    if not (os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")):
        pytest.skip("TEST_DATABASE_URL or DATABASE_URL is required")
    database = Database.from_env()
    account_id = None
    try:
        async with database.transaction() as session:
            account, _source, destination, _execution, job = await seed_job(
                session,
                rendered_text="تعليق الألبوم",
                content_type=ContentType.ALBUM,
                media_json=[
                    {
                        "media_type": "photo",
                        "identity": "photo:a",
                        "source_message_id": 3201,
                    },
                    {
                        "media_type": "video",
                        "identity": "video:b",
                        "source_message_id": 3202,
                    },
                ],
            )
            account_id = account.id
            job_id = job.id

        bot = FakeBot()
        _queue, _stager, worker = make_worker(
            database, account_id, tmp_path, bot, FakeUserAdapter()
        )
        assert await worker.run_once() == 1

        assert bot.sent_albums == [
            (destination.telegram_chat_id, ["photo", "video"], "تعليق الألبوم")
        ]
        async with database.session() as session:
            published = await session.scalar(
                select(PublishedMessage).where(PublishedMessage.publish_job_id == job_id)
            )
            assert published is not None
            assert published.metadata_json["message_count"] == 2
    finally:
        if account_id is not None:
            async with database.transaction() as session:
                await session.execute(delete(Account).where(Account.id == account_id))
        await database.close()


@pytest.mark.asyncio
async def test_retry_after_is_the_only_safe_retry_after_external_publish_start(tmp_path):
    if not (os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")):
        pytest.skip("TEST_DATABASE_URL or DATABASE_URL is required")
    database = Database.from_env()
    account_id = None
    try:
        async with database.transaction() as session:
            account, _source, _destination, execution, job = await seed_job(
                session,
                rendered_text="خبر ينتظر",
            )
            account_id = account.id
            job_id = job.id
            execution_id = execution.id

        bot = FakeBot(fail_on_call=1, failure=RetryAfter(7))
        _queue, _stager, worker = make_worker(
            database, account_id, tmp_path, bot, FakeUserAdapter()
        )
        assert await worker.run_once() == 1

        async with database.session() as session:
            stored_job = await session.get(PublishJob, job_id)
            stored_execution = await session.get(RouteExecution, execution_id)
            attempt = await session.scalar(
                select(PublicationAttempt).where(
                    PublicationAttempt.publish_job_id == job_id
                )
            )
            assert stored_job is not None
            assert stored_job.status is JobStatus.RETRY_WAIT
            assert stored_job.last_error_code == "telegram_retry_after"
            assert stored_job.next_attempt_at is not None
            assert stored_execution is not None
            assert stored_execution.status is RouteExecutionStatus.QUEUED
            assert attempt is not None
            assert attempt.status is AttemptStatus.RETRYING
    finally:
        if account_id is not None:
            async with database.transaction() as session:
                await session.execute(delete(Account).where(Account.id == account_id))
        await database.close()


@pytest.mark.asyncio
async def test_partial_text_publish_fails_closed_and_records_confirmed_ids(tmp_path):
    if not (os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")):
        pytest.skip("TEST_DATABASE_URL or DATABASE_URL is required")
    database = Database.from_env()
    account_id = None
    try:
        long_text = ("أ" * 4096) + " " + ("ب" * 50)
        async with database.transaction() as session:
            account, _source, _destination, execution, job = await seed_job(
                session,
                rendered_text=long_text,
            )
            account_id = account.id
            job_id = job.id
            execution_id = execution.id

        bot = FakeBot(fail_on_call=2, failure=BadRequest("rejected second chunk"))
        _queue, _stager, worker = make_worker(
            database, account_id, tmp_path, bot, FakeUserAdapter()
        )
        assert await worker.run_once() == 1

        async with database.session() as session:
            stored_job = await session.get(PublishJob, job_id)
            stored_execution = await session.get(RouteExecution, execution_id)
            published = await session.scalar(
                select(PublishedMessage).where(PublishedMessage.publish_job_id == job_id)
            )
            assert stored_job is not None
            assert stored_job.status is JobStatus.FAILED
            assert stored_job.last_error_code == "partial_publish"
            assert stored_execution is not None
            assert stored_execution.status is RouteExecutionStatus.FAILED
            assert published is not None
            assert published.metadata_json["partial"] is True
            assert published.metadata_json["message_count"] == 1
    finally:
        if account_id is not None:
            async with database.transaction() as session:
                await session.execute(delete(Account).where(Account.id == account_id))
        await database.close()


@pytest.mark.asyncio
async def test_expired_publishing_lease_fails_unknown_instead_of_retrying(tmp_path):
    if not (os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")):
        pytest.skip("TEST_DATABASE_URL or DATABASE_URL is required")
    database = Database.from_env()
    account_id = None
    start = datetime(2026, 10, 9, 1, 0, tzinfo=UTC)
    try:
        async with database.transaction() as session:
            account, _source, _destination, execution, job = await seed_job(
                session,
                rendered_text="خبر حالة غير مؤكدة",
            )
            account_id = account.id
            job_id = job.id
            execution_id = execution.id

        queue = PublishQueueV3(
            database.session_factory,
            account_id,
            default_lease_seconds=10,
        )
        claimed = await queue.claim_due(
            worker_id="publisher-crash",
            now=start,
            lease_seconds=10,
        )
        assert [item.id for item in claimed] == [job_id]
        await queue.mark_publishing(
            job_id,
            worker_id="publisher-crash",
            now=start,
            lease_seconds=10,
        )
        assert await queue.recover_expired_leases(
            now=start + timedelta(seconds=11)
        ) == 1

        async with database.session() as session:
            stored_job = await session.get(PublishJob, job_id)
            stored_execution = await session.get(RouteExecution, execution_id)
            assert stored_job is not None
            assert stored_job.status is JobStatus.FAILED
            assert stored_job.last_error_code == "publish_outcome_unknown"
            assert stored_execution is not None
            assert stored_execution.status is RouteExecutionStatus.FAILED
    finally:
        if account_id is not None:
            async with database.transaction() as session:
                await session.execute(delete(Account).where(Account.id == account_id))
        await database.close()
