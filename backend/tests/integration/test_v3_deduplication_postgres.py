from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select

from app.db import Database
from app.db.models import (
    Account,
    ContentType,
    DeduplicationProfile,
    Destination,
    DestinationStatus,
    Project,
    RouteExecution,
    RouteExecutionStatus,
    RouteFingerprint,
    RouteStatus,
    Source,
    SourceCheckpoint,
    SourceRoute,
    TelegramAccount,
)
from app.v3.content import NormalizedMedia, ProcessedContent, ProcessingDecision, ProcessingResult
from app.v3.deduplication import DeduplicationCoordinator, DeduplicationDecision, FingerprintType
from app.v3.domain import RouteExecutionService

pytestmark = pytest.mark.integration


def processed(text: str = "", *media_ids: str) -> ProcessedContent:
    media = tuple(
        NormalizedMedia(
            media_type="photo",
            identity=identity,
            source_message_id=index + 1,
        )
        for index, identity in enumerate(media_ids)
    )
    return ProcessedContent(
        normalized_text=text,
        rendered_text=text,
        content_type=ContentType.PHOTO if media else ContentType.TEXT,
        media=media,
    )


async def seed(
    session,
    *,
    source_count: int = 1,
    profile: DeduplicationProfile | None = None,
):
    account = Account(name="V3 Dedup", slug=f"dedup-{uuid.uuid4().hex[:10]}")
    session.add(account)
    await session.flush()

    project = Project(
        account_id=account.id,
        name="Dedup Project",
        slug=f"project-{uuid.uuid4().hex[:10]}",
    )
    telegram = TelegramAccount(
        account_id=account.id,
        label=f"telegram-{uuid.uuid4().hex[:8]}",
        session_key=f"session-{uuid.uuid4()}",
    )
    session.add_all([project, telegram])
    if profile is not None:
        profile.account_id = account.id
        session.add(profile)
    await session.flush()

    destination = Destination(
        account_id=account.id,
        project_id=project.id,
        name="Destination",
        telegram_chat_id=-1005000000001,
        status=DestinationStatus.ACTIVE,
    )
    session.add(destination)
    await session.flush()

    sources = []
    routes = []
    for index in range(source_count):
        source = Source(
            account_id=account.id,
            telegram_account_id=telegram.id,
            telegram_chat_id=-1006000000000 - index,
            title=f"Source {index}",
        )
        session.add(source)
        await session.flush()
        route = SourceRoute(
            account_id=account.id,
            source_id=source.id,
            destination_id=destination.id,
            status=RouteStatus.ACTIVE,
            deduplication_profile_id=profile.id if profile is not None else None,
        )
        session.add(route)
        await session.flush()
        sources.append(source)
        routes.append(route)
    return account, destination, tuple(sources), tuple(routes)


async def make_ready(
    session,
    *,
    account_id: uuid.UUID,
    source: Source,
    message_id: int,
    body: ProcessedContent,
) -> ProcessingResult:
    service = RouteExecutionService(session, account_id)
    registration = await service.register_event(
        source_id=source.id,
        cursor_message_id=message_id,
        telegram_message_id=message_id,
    )
    assert len(registration.executions) == 1
    execution = registration.executions[0]
    await service.transition(execution.id, RouteExecutionStatus.PROCESSING)
    await service.transition(execution.id, RouteExecutionStatus.READY_FOR_DEDUP)
    checkpoint = await session.get(SourceCheckpoint, source.id)
    if checkpoint is None:
        session.add(
            SourceCheckpoint(
                source_id=source.id,
                last_seen_message_id=message_id,
                last_committed_message_id=0,
            )
        )
    elif message_id > checkpoint.last_seen_message_id:
        checkpoint.last_seen_message_id = message_id
    await session.flush()
    return ProcessingResult(
        execution_id=execution.id,
        decision=ProcessingDecision.READY_FOR_DEDUP,
        reason_code="ready_for_dedup",
        content=body,
    )


@pytest.mark.asyncio
async def test_distinct_text_only_messages_do_not_match_empty_media_fingerprint():
    if not (os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")):
        pytest.skip("TEST_DATABASE_URL or DATABASE_URL is required")
    database = Database.from_env()
    account_id = None
    try:
        async with database.transaction() as session:
            account, _destination, sources, _routes = await seed(session)
            account_id = account.id
            first = await make_ready(
                session,
                account_id=account.id,
                source=sources[0],
                message_id=1001,
                body=processed("الخبر الأول"),
            )
            second = await make_ready(
                session,
                account_id=account.id,
                source=sources[0],
                message_id=1002,
                body=processed("الخبر الثاني"),
            )

        coordinator = DeduplicationCoordinator(database.session_factory, account_id)
        first_result = await coordinator.process(first)
        second_result = await coordinator.process(second)

        assert first_result.decision is DeduplicationDecision.READY_FOR_QUEUE
        assert second_result.decision is DeduplicationDecision.READY_FOR_QUEUE

        async with database.session() as session:
            rows = (
                await session.scalars(
                    select(RouteFingerprint).where(RouteFingerprint.account_id == account_id)
                )
            ).all()
            assert rows
            assert {row.fingerprint_type for row in rows}.isdisjoint({"media"})
            executions = (
                await session.scalars(
                    select(RouteExecution).where(RouteExecution.account_id == account_id)
                )
            ).all()
            assert {item.status for item in executions} == {
                RouteExecutionStatus.READY_FOR_QUEUE
            }
    finally:
        if account_id is not None:
            async with database.transaction() as session:
                await session.execute(delete(Account).where(Account.id == account_id))
        await database.close()


@pytest.mark.asyncio
async def test_matching_text_is_duplicate_with_explicit_reason():
    if not (os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")):
        pytest.skip("TEST_DATABASE_URL or DATABASE_URL is required")
    database = Database.from_env()
    account_id = None
    try:
        async with database.transaction() as session:
            account, _destination, sources, _routes = await seed(session)
            account_id = account.id
            first = await make_ready(
                session,
                account_id=account.id,
                source=sources[0],
                message_id=1101,
                body=processed("نفس الخبر"),
            )
            second = await make_ready(
                session,
                account_id=account.id,
                source=sources[0],
                message_id=1102,
                body=processed("نفس الخبر"),
            )

        coordinator = DeduplicationCoordinator(database.session_factory, account_id)
        assert (await coordinator.process(first)).decision is DeduplicationDecision.READY_FOR_QUEUE
        duplicate = await coordinator.process(second)

        assert duplicate.decision is DeduplicationDecision.DUPLICATE
        assert duplicate.matched_type is FingerprintType.TEXT
        assert duplicate.reason_code == "duplicate_matching_text"

        async with database.session() as session:
            stored = await session.get(RouteExecution, second.execution_id)
            checkpoint = await session.get(SourceCheckpoint, sources[0].id)
            assert stored is not None
            assert stored.status is RouteExecutionStatus.DUPLICATE
            assert checkpoint is not None
            assert checkpoint.last_committed_message_id == 0
            # The earlier unique route is still waiting for Phase 6 queue durability.
    finally:
        if account_id is not None:
            async with database.transaction() as session:
                await session.execute(delete(Account).where(Account.id == account_id))
        await database.close()


@pytest.mark.asyncio
async def test_media_only_messages_do_not_match_empty_text_and_real_media_duplicate_is_detected():
    if not (os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")):
        pytest.skip("TEST_DATABASE_URL or DATABASE_URL is required")
    database = Database.from_env()
    account_id = None
    try:
        async with database.transaction() as session:
            account, _destination, sources, _routes = await seed(session)
            account_id = account.id
            first = await make_ready(
                session,
                account_id=account.id,
                source=sources[0],
                message_id=1201,
                body=processed("", "photo:a"),
            )
            second = await make_ready(
                session,
                account_id=account.id,
                source=sources[0],
                message_id=1202,
                body=processed("", "photo:b"),
            )
            third = await make_ready(
                session,
                account_id=account.id,
                source=sources[0],
                message_id=1203,
                body=processed("", "photo:a"),
            )

        coordinator = DeduplicationCoordinator(database.session_factory, account_id)
        assert (await coordinator.process(first)).decision is DeduplicationDecision.READY_FOR_QUEUE
        assert (await coordinator.process(second)).decision is DeduplicationDecision.READY_FOR_QUEUE
        duplicate = await coordinator.process(third)

        assert duplicate.decision is DeduplicationDecision.DUPLICATE
        assert duplicate.matched_type is FingerprintType.MEDIA

        async with database.session() as session:
            types = set(
                (
                    await session.scalars(
                        select(RouteFingerprint.fingerprint_type).where(
                            RouteFingerprint.account_id == account_id
                        )
                    )
                ).all()
            )
            assert "text" not in types
            assert "media" in types
    finally:
        if account_id is not None:
            async with database.transaction() as session:
                await session.execute(delete(Account).where(Account.id == account_id))
        await database.close()


@pytest.mark.asyncio
async def test_concurrent_matching_messages_allow_only_one_unique_winner():
    if not (os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")):
        pytest.skip("TEST_DATABASE_URL or DATABASE_URL is required")
    database = Database.from_env()
    account_id = None
    try:
        async with database.transaction() as session:
            account, _destination, sources, _routes = await seed(session)
            account_id = account.id
            first = await make_ready(
                session,
                account_id=account.id,
                source=sources[0],
                message_id=1301,
                body=processed("خبر متزامن"),
            )
            second = await make_ready(
                session,
                account_id=account.id,
                source=sources[0],
                message_id=1302,
                body=processed("خبر متزامن"),
            )

        coordinator = DeduplicationCoordinator(database.session_factory, account_id)
        results = await asyncio.gather(
            coordinator.process(first),
            coordinator.process(second),
        )

        assert {item.decision for item in results} == {
            DeduplicationDecision.READY_FOR_QUEUE,
            DeduplicationDecision.DUPLICATE,
        }
    finally:
        if account_id is not None:
            async with database.transaction() as session:
                await session.execute(delete(Account).where(Account.id == account_id))
        await database.close()


@pytest.mark.asyncio
async def test_content_duplicate_window_expires():
    if not (os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")):
        pytest.skip("TEST_DATABASE_URL or DATABASE_URL is required")
    database = Database.from_env()
    account_id = None
    start = datetime(2026, 10, 8, 0, 0, tzinfo=UTC)
    try:
        profile = DeduplicationProfile(
            account_id=uuid.uuid4(),
            name="Short Window",
            enabled=True,
            window_seconds=10,
            compare_text=True,
            compare_media=True,
            compare_telegram_id=True,
            options={},
        )
        async with database.transaction() as session:
            account, _destination, sources, _routes = await seed(session, profile=profile)
            account_id = account.id
            first = await make_ready(
                session,
                account_id=account.id,
                source=sources[0],
                message_id=1401,
                body=processed("نافذة زمنية"),
            )
            second = await make_ready(
                session,
                account_id=account.id,
                source=sources[0],
                message_id=1402,
                body=processed("نافذة زمنية"),
            )

        first_coordinator = DeduplicationCoordinator(
            database.session_factory,
            account_id,
            clock=lambda: start,
        )
        second_coordinator = DeduplicationCoordinator(
            database.session_factory,
            account_id,
            clock=lambda: start + timedelta(seconds=11),
        )
        assert (
            await first_coordinator.process(first)
        ).decision is DeduplicationDecision.READY_FOR_QUEUE
        assert (
            await second_coordinator.process(second)
        ).decision is DeduplicationDecision.READY_FOR_QUEUE
    finally:
        if account_id is not None:
            async with database.transaction() as session:
                await session.execute(delete(Account).where(Account.id == account_id))
        await database.close()


@pytest.mark.asyncio
async def test_destination_scope_enables_explicit_cross_source_deduplication():
    if not (os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")):
        pytest.skip("TEST_DATABASE_URL or DATABASE_URL is required")
    database = Database.from_env()
    account_id = None
    try:
        profile = DeduplicationProfile(
            account_id=uuid.uuid4(),
            name="Destination Scope",
            enabled=True,
            window_seconds=86400,
            compare_text=True,
            compare_media=True,
            compare_telegram_id=True,
            options={"scope": "destination"},
        )
        async with database.transaction() as session:
            account, _destination, sources, _routes = await seed(
                session,
                source_count=2,
                profile=profile,
            )
            account_id = account.id
            first = await make_ready(
                session,
                account_id=account.id,
                source=sources[0],
                message_id=1501,
                body=processed("خبر من مصدرين"),
            )
            second = await make_ready(
                session,
                account_id=account.id,
                source=sources[1],
                message_id=1502,
                body=processed("خبر من مصدرين"),
            )

        coordinator = DeduplicationCoordinator(database.session_factory, account_id)
        assert (await coordinator.process(first)).decision is DeduplicationDecision.READY_FOR_QUEUE
        duplicate = await coordinator.process(second)
        assert duplicate.decision is DeduplicationDecision.DUPLICATE
        assert duplicate.matched_type is FingerprintType.TEXT
    finally:
        if account_id is not None:
            async with database.transaction() as session:
                await session.execute(delete(Account).where(Account.id == account_id))
        await database.close()
