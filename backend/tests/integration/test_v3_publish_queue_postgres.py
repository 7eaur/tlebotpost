from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select

from app.db import Database
from app.db.models import (
    Account,
    AttemptStatus,
    Destination,
    DestinationStatus,
    JobStatus,
    Project,
    PublicationAttempt,
    PublishingMode,
    PublishJob,
    RouteExecution,
    RouteExecutionStatus,
    RoutePublishPayload,
    RouteStatus,
    Source,
    SourceCheckpoint,
    SourceRoute,
    TelegramAccount,
)
from app.v3.content import ContentProcessingCoordinator
from app.v3.deduplication import (
    DeduplicationCoordinator,
    DeduplicationDecision,
    DeduplicationResult,
)
from app.v3.domain import RouteExecutionService
from app.v3.publish_queue import (
    PublishQueueV3,
    QueueHandoffDecision,
    QueueReliabilityRecoveryComponent,
)
from app.v3.telegram.snapshots import SourceEventSnapshotStore
from app.v3.telegram.types import SourceEvent

pytestmark = pytest.mark.integration


def message(message_id: int, text: str):
    return SimpleNamespace(
        id=message_id,
        grouped_id=None,
        date=datetime(2026, 10, 8, 2, 0, tzinfo=UTC),
        raw_text=text,
        media=None,
    )


async def seed(
    session,
    *,
    publishing_mode: PublishingMode = PublishingMode.DIRECT,
    route_settings: dict | None = None,
):
    account = Account(name="V3 Queue", slug=f"queue-{uuid.uuid4().hex[:10]}")
    session.add(account)
    await session.flush()

    project = Project(
        account_id=account.id,
        name="Queue Project",
        slug=f"queue-project-{uuid.uuid4().hex[:8]}",
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
        telegram_chat_id=-1007000000001,
        title="Queue Source",
    )
    destination = Destination(
        account_id=account.id,
        project_id=project.id,
        name="Queue Destination",
        telegram_chat_id=-1008000000001,
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
        publishing_mode=publishing_mode,
        settings=route_settings or {},
    )
    session.add(route)
    await session.flush()
    return account, source, destination, route


async def register_event(session, *, account_id, source, message_id: int):
    checkpoint = await session.get(SourceCheckpoint, source.id)
    if checkpoint is None:
        checkpoint = SourceCheckpoint(
            source_id=source.id,
            last_seen_message_id=message_id,
            last_committed_message_id=0,
        )
        session.add(checkpoint)
    else:
        checkpoint.last_seen_message_id = max(checkpoint.last_seen_message_id, message_id)

    registration = await RouteExecutionService(session, account_id).register_event(
        source_id=source.id,
        cursor_message_id=message_id,
        telegram_message_id=message_id,
    )
    await session.flush()
    return registration


def source_event(account_id, source, message_id: int, text: str) -> SourceEvent:
    return SourceEvent.from_messages(
        account_id=account_id,
        source_id=source.id,
        chat_id=source.telegram_chat_id,
        messages=(message(message_id, text),),
    )


async def run_pipeline(database, account_id, event, registration, queue):
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
    await processor.process_registration(event, registration)


@pytest.mark.asyncio
async def test_full_phase6_handoff_persists_payload_job_and_checkpoint_idempotently():
    if not (os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")):
        pytest.skip("TEST_DATABASE_URL or DATABASE_URL is required")
    database = Database.from_env()
    account_id = None
    try:
        async with database.transaction() as session:
            account, source, _destination, _route = await seed(session)
            account_id = account.id
            registration = await register_event(
                session,
                account_id=account.id,
                source=source,
                message_id=2001,
            )

        event = source_event(account_id, source, 2001, "خبر دائم للصف")
        queue = PublishQueueV3(database.session_factory, account_id)
        await run_pipeline(database, account_id, event, registration, queue)

        execution_id = registration.executions[0].id
        async with database.session() as session:
            execution = await session.get(RouteExecution, execution_id)
            payload = await session.scalar(
                select(RoutePublishPayload).where(
                    RoutePublishPayload.route_execution_id == execution_id
                )
            )
            jobs = (
                await session.scalars(
                    select(PublishJob).where(PublishJob.route_execution_id == execution_id)
                )
            ).all()
            checkpoint = await session.get(SourceCheckpoint, source.id)

            assert execution is not None
            assert execution.status is RouteExecutionStatus.QUEUED
            assert payload is not None
            assert payload.rendered_text == "خبر دائم للصف"
            assert len(jobs) == 1
            assert jobs[0].content_item_id is None
            assert jobs[0].route_payload_id == payload.id
            assert jobs[0].status is JobStatus.QUEUED
            assert execution.publish_job_id == jobs[0].id
            assert checkpoint is not None
            assert checkpoint.last_committed_message_id == 2001

        replay = await queue.enqueue_result(
            DeduplicationResult(
                execution_id=execution_id,
                decision=DeduplicationDecision.READY_FOR_QUEUE,
                reason_code="dedup_unique",
            )
        )
        assert replay is not None
        assert replay.decision is QueueHandoffDecision.ALREADY_ENQUEUED

        async with database.session() as session:
            count = len(
                (
                    await session.scalars(
                        select(PublishJob).where(PublishJob.route_execution_id == execution_id)
                    )
                ).all()
            )
            assert count == 1
    finally:
        if account_id is not None:
            async with database.transaction() as session:
                await session.execute(delete(Account).where(Account.id == account_id))
        await database.close()


@pytest.mark.asyncio
async def test_startup_recovery_closes_processing_to_dedup_and_dedup_to_queue_crash_windows():
    if not (os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")):
        pytest.skip("TEST_DATABASE_URL or DATABASE_URL is required")
    database = Database.from_env()
    account_id = None
    try:
        async with database.transaction() as session:
            account, source, _destination, _route = await seed(session)
            account_id = account.id
            first_registration = await register_event(
                session,
                account_id=account.id,
                source=source,
                message_id=2101,
            )
            second_registration = await register_event(
                session,
                account_id=account.id,
                source=source,
                message_id=2102,
            )
            third_registration = await register_event(
                session,
                account_id=account.id,
                source=source,
                message_id=2103,
            )
            third_event = source_event(account.id, source, 2103, "الرسالة الثالثة")
            await SourceEventSnapshotStore().persist(
                session,
                event=third_event,
                event_key=third_registration.event_key,
            )

        processor_only = ContentProcessingCoordinator(database.session_factory, account_id)
        await processor_only.process_registration(
            source_event(account_id, source, 2101, "الرسالة الأولى"),
            first_registration,
        )

        dedup_without_queue = DeduplicationCoordinator(database.session_factory, account_id)
        processor_to_dedup = ContentProcessingCoordinator(
            database.session_factory,
            account_id,
            on_ready=dedup_without_queue.process,
        )
        await processor_to_dedup.process_registration(
            source_event(account_id, source, 2102, "الرسالة الثانية"),
            second_registration,
        )

        async with database.session() as session:
            first = await session.get(RouteExecution, first_registration.executions[0].id)
            second = await session.get(RouteExecution, second_registration.executions[0].id)
            assert first is not None and first.status is RouteExecutionStatus.READY_FOR_DEDUP
            third = await session.get(RouteExecution, third_registration.executions[0].id)
            assert second is not None and second.status is RouteExecutionStatus.READY_FOR_QUEUE
            assert third is not None and third.status is RouteExecutionStatus.RECEIVED
            assert (
                await session.scalar(
                    select(RoutePublishPayload.id).where(
                        RoutePublishPayload.route_execution_id == first.id
                    )
                )
                is not None
            )
            assert (
                await session.scalar(
                    select(RoutePublishPayload.id).where(
                        RoutePublishPayload.route_execution_id == second.id
                    )
                )
                is not None
            )

        queue = PublishQueueV3(database.session_factory, account_id)
        dedup = DeduplicationCoordinator(
            database.session_factory,
            account_id,
            on_ready=queue.enqueue_result,
        )
        recovery_processor = ContentProcessingCoordinator(
            database.session_factory,
            account_id,
            on_ready=dedup.process,
        )
        recovery = QueueReliabilityRecoveryComponent(
            processing=recovery_processor,
            deduplication=dedup,
            queue=queue,
        )
        await recovery.start()

        async with database.session() as session:
            executions = (
                await session.scalars(
                    select(RouteExecution)
                    .where(RouteExecution.account_id == account_id)
                    .order_by(RouteExecution.cursor_message_id)
                )
            ).all()
            jobs = (
                await session.scalars(
                    select(PublishJob).where(PublishJob.account_id == account_id)
                )
            ).all()
            checkpoint = await session.get(SourceCheckpoint, source.id)
            assert [item.status for item in executions] == [
                RouteExecutionStatus.QUEUED,
                RouteExecutionStatus.QUEUED,
                RouteExecutionStatus.QUEUED,
            ]
            assert len(jobs) == 3
            assert checkpoint is not None
            assert checkpoint.last_committed_message_id == 2103
        await recovery.stop()
    finally:
        if account_id is not None:
            async with database.transaction() as session:
                await session.execute(delete(Account).where(Account.id == account_id))
        await database.close()


@pytest.mark.asyncio
async def test_claim_retry_honors_retry_after_and_max_attempts():
    if not (os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")):
        pytest.skip("TEST_DATABASE_URL or DATABASE_URL is required")
    database = Database.from_env()
    account_id = None
    start = datetime(2026, 10, 8, 3, 0, tzinfo=UTC)
    try:
        async with database.transaction() as session:
            account, source, _destination, _route = await seed(
                session,
                route_settings={"max_attempts": 2},
            )
            account_id = account.id
            registration = await register_event(
                session,
                account_id=account.id,
                source=source,
                message_id=2201,
            )

        queue = PublishQueueV3(
            database.session_factory,
            account_id,
            retry_base_seconds=30,
            retry_cap_seconds=300,
        )
        await run_pipeline(
            database,
            account_id,
            source_event(account_id, source, 2201, "خبر لإعادة المحاولة"),
            registration,
            queue,
        )

        first_claim = await queue.claim_due(worker_id="worker-a", now=start, lease_seconds=60)
        assert len(first_claim) == 1
        job_id = first_claim[0].id
        assert await queue.claim_due(worker_id="worker-b", now=start) == []

        status = await queue.mark_retry(
            job_id,
            worker_id="worker-a",
            error_code="retry_after",
            error_message="Telegram asked the worker to wait",
            now=start,
            retry_after_seconds=120,
        )
        assert status is JobStatus.RETRY_WAIT

        assert await queue.claim_due(
            worker_id="worker-b",
            now=start + timedelta(seconds=119),
        ) == []
        second_claim = await queue.claim_due(
            worker_id="worker-b",
            now=start + timedelta(seconds=120),
        )
        assert len(second_claim) == 1
        assert second_claim[0].attempt_count == 2

        final_status = await queue.mark_retry(
            job_id,
            worker_id="worker-b",
            error_code="temporary_error",
            error_message="still unavailable",
            now=start + timedelta(seconds=120),
        )
        assert final_status is JobStatus.FAILED

        async with database.session() as session:
            job = await session.get(PublishJob, job_id)
            execution = await session.get(RouteExecution, registration.executions[0].id)
            attempts = (
                await session.scalars(
                    select(PublicationAttempt)
                    .where(PublicationAttempt.publish_job_id == job_id)
                    .order_by(PublicationAttempt.attempt_number)
                )
            ).all()
            assert job is not None
            assert job.status is JobStatus.FAILED
            assert job.max_attempts == 2
            assert job.locked_by is None
            assert job.lease_expires_at is None
            assert execution is not None
            assert execution.status is RouteExecutionStatus.FAILED
            assert [item.status for item in attempts] == [
                AttemptStatus.RETRYING,
                AttemptStatus.FAILED,
            ]
    finally:
        if account_id is not None:
            async with database.transaction() as session:
                await session.execute(delete(Account).where(Account.id == account_id))
        await database.close()


@pytest.mark.asyncio
async def test_expired_lease_is_recovered_to_retry_wait_and_can_be_reclaimed():
    if not (os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")):
        pytest.skip("TEST_DATABASE_URL or DATABASE_URL is required")
    database = Database.from_env()
    account_id = None
    start = datetime(2026, 10, 8, 4, 0, tzinfo=UTC)
    try:
        async with database.transaction() as session:
            account, source, _destination, _route = await seed(
                session,
                route_settings={"max_attempts": 3},
            )
            account_id = account.id
            registration = await register_event(
                session,
                account_id=account.id,
                source=source,
                message_id=2301,
            )

        queue = PublishQueueV3(
            database.session_factory,
            account_id,
            retry_base_seconds=30,
            retry_cap_seconds=300,
        )
        await run_pipeline(
            database,
            account_id,
            source_event(account_id, source, 2301, "خبر lease"),
            registration,
            queue,
        )
        claim = await queue.claim_due(worker_id="worker-a", now=start, lease_seconds=10)
        assert len(claim) == 1
        job_id = claim[0].id

        assert (
            await queue.recover_expired_leases(now=start + timedelta(seconds=11))
            == 1
        )
        assert await queue.claim_due(
            worker_id="worker-b",
            now=start + timedelta(seconds=40),
        ) == []
        reclaimed = await queue.claim_due(
            worker_id="worker-b",
            now=start + timedelta(seconds=41),
        )
        assert len(reclaimed) == 1
        assert reclaimed[0].id == job_id
        assert reclaimed[0].attempt_count == 2
    finally:
        if account_id is not None:
            async with database.transaction() as session:
                await session.execute(delete(Account).where(Account.id == account_id))
        await database.close()


@pytest.mark.asyncio
async def test_manual_mode_is_durable_hold_until_explicit_release():
    if not (os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")):
        pytest.skip("TEST_DATABASE_URL or DATABASE_URL is required")
    database = Database.from_env()
    account_id = None
    now = datetime(2026, 10, 8, 5, 0, tzinfo=UTC)
    try:
        async with database.transaction() as session:
            account, source, _destination, _route = await seed(
                session,
                publishing_mode=PublishingMode.MANUAL,
            )
            account_id = account.id
            registration = await register_event(
                session,
                account_id=account.id,
                source=source,
                message_id=2401,
            )

        queue = PublishQueueV3(database.session_factory, account_id)
        await run_pipeline(
            database,
            account_id,
            source_event(account_id, source, 2401, "خبر يدوي"),
            registration,
            queue,
        )

        async with database.session() as session:
            job = await session.scalar(
                select(PublishJob).where(PublishJob.account_id == account_id)
            )
            checkpoint = await session.get(SourceCheckpoint, source.id)
            assert job is not None
            assert job.status is JobStatus.MANUAL_HOLD
            assert checkpoint is not None
            assert checkpoint.last_committed_message_id == 2401
            job_id = job.id

        assert await queue.claim_due(worker_id="worker-a", now=now) == []
        await queue.release_manual(job_id, now=now)
        claimed = await queue.claim_due(worker_id="worker-a", now=now)
        assert len(claimed) == 1
        assert claimed[0].id == job_id
    finally:
        if account_id is not None:
            async with database.transaction() as session:
                await session.execute(delete(Account).where(Account.id == account_id))
        await database.close()
