from __future__ import annotations

import os
import uuid

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
    TelegramAccount,
)
from app.v3.domain import RouteExecutionError, RouteExecutionService, SourceCheckpointCoordinator

pytestmark = pytest.mark.integration


async def _seed(session, *, route_count: int = 2):
    account = Account(name="V3 Test", slug=f"v3-{uuid.uuid4().hex[:12]}")
    session.add(account)
    await session.flush()

    project = Project(
        account_id=account.id,
        name="V3 Project",
        slug=f"project-{uuid.uuid4().hex[:10]}",
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
        telegram_chat_id=-1001000000001,
        title="Source",
    )
    session.add(source)
    await session.flush()

    routes = []
    for index in range(route_count):
        destination = Destination(
            account_id=account.id,
            project_id=project.id,
            name=f"Destination {index + 1}",
            telegram_chat_id=-1002000000000 - index,
            status=DestinationStatus.ACTIVE,
        )
        session.add(destination)
        await session.flush()
        route = SourceRoute(
            account_id=account.id,
            source_id=source.id,
            destination_id=destination.id,
            status=RouteStatus.ACTIVE,
            priority=100 + index,
        )
        session.add(route)
        await session.flush()
        routes.append(route)

    return account, source, tuple(routes)


@pytest.mark.asyncio
async def test_route_fanout_blocks_checkpoint_until_every_route_is_safe():
    database_url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL or DATABASE_URL is required")

    database = Database.from_env()
    account_id = None
    try:
        async with database.transaction() as session:
            account, source, _routes = await _seed(session, route_count=2)
            account_id = account.id
            service = RouteExecutionService(session, account.id)
            checkpoint = SourceCheckpointCoordinator(session, account.id)

            registration = await service.register_event(
                source_id=source.id,
                cursor_message_id=101,
                telegram_message_id=101,
            )
            assert len(registration.executions) == 2
            assert {item.status for item in registration.executions} == {
                RouteExecutionStatus.RECEIVED
            }

            repeated = await service.register_event(
                source_id=source.id,
                cursor_message_id=101,
                telegram_message_id=101,
            )
            assert {item.id for item in repeated.executions} == {
                item.id for item in registration.executions
            }

            await service.transition(
                registration.executions[0].id,
                RouteExecutionStatus.QUEUED,
                reason_code="publish_job_persisted",
            )
            assert not await checkpoint.try_advance(
                source_id=source.id,
                cursor_message_id=101,
            )

            await service.transition(
                registration.executions[1].id,
                RouteExecutionStatus.FILTERED,
                reason_code="route_filter",
            )
            assert await checkpoint.try_advance(
                source_id=source.id,
                cursor_message_id=101,
            )

            stored = await session.get(SourceCheckpoint, source.id)
            assert stored is not None
            assert stored.last_committed_message_id == 101
    finally:
        if account_id is not None:
            async with database.transaction() as session:
                await session.execute(delete(Account).where(Account.id == account_id))
        await database.close()


@pytest.mark.asyncio
async def test_checkpoint_cannot_jump_over_older_unfinished_event():
    database_url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL or DATABASE_URL is required")

    database = Database.from_env()
    account_id = None
    try:
        async with database.transaction() as session:
            account, source, _routes = await _seed(session, route_count=1)
            account_id = account.id
            service = RouteExecutionService(session, account.id)
            checkpoint = SourceCheckpointCoordinator(session, account.id)

            older = await service.register_event(
                source_id=source.id,
                cursor_message_id=201,
                telegram_message_id=201,
            )
            newer = await service.register_event(
                source_id=source.id,
                cursor_message_id=202,
                telegram_message_id=202,
            )
            await service.transition(
                newer.executions[0].id,
                RouteExecutionStatus.QUEUED,
            )

            assert not await checkpoint.try_advance(
                source_id=source.id,
                cursor_message_id=202,
            )

            await service.transition(
                older.executions[0].id,
                RouteExecutionStatus.DUPLICATE,
                reason_code="exact_identity",
            )
            assert await checkpoint.try_advance(
                source_id=source.id,
                cursor_message_id=201,
            )
            caught_up = await session.get(SourceCheckpoint, source.id)
            assert caught_up is not None
            assert caught_up.last_committed_message_id == 202
            assert await checkpoint.try_advance(
                source_id=source.id,
                cursor_message_id=202,
            )

            stored = await session.get(SourceCheckpoint, source.id)
            assert stored is not None
            assert stored.last_committed_message_id == 202
    finally:
        if account_id is not None:
            async with database.transaction() as session:
                await session.execute(delete(Account).where(Account.id == account_id))
        await database.close()


@pytest.mark.asyncio
async def test_route_execution_is_account_scoped():
    database_url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL or DATABASE_URL is required")

    database = Database.from_env()
    account_ids: list[uuid.UUID] = []
    try:
        async with database.transaction() as session:
            account, source, _routes = await _seed(session, route_count=1)
            other = Account(name="Other", slug=f"other-{uuid.uuid4().hex[:12]}")
            session.add(other)
            await session.flush()
            account_ids.extend([account.id, other.id])

            owner_service = RouteExecutionService(session, account.id)
            registration = await owner_service.register_event(
                source_id=source.id,
                cursor_message_id=301,
                telegram_message_id=301,
            )

            other_service = RouteExecutionService(session, other.id)
            with pytest.raises(RouteExecutionError, match="not available"):
                await other_service.transition(
                    registration.executions[0].id,
                    RouteExecutionStatus.QUEUED,
                )

            count = len(
                (
                    await session.scalars(
                        select(RouteExecution).where(RouteExecution.account_id == account.id)
                    )
                ).all()
            )
            assert count == 1
    finally:
        for account_id in account_ids:
            async with database.transaction() as session:
                await session.execute(delete(Account).where(Account.id == account_id))
        await database.close()


@pytest.mark.asyncio
async def test_paused_project_creates_no_new_route_execution():
    database_url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL or DATABASE_URL is required")

    database = Database.from_env()
    account_id = None
    try:
        async with database.transaction() as session:
            account, source, _routes = await _seed(session, route_count=1)
            account_id = account.id
            project = await session.scalar(
                select(Project).where(Project.account_id == account.id)
            )
            assert project is not None
            project.status = ProjectStatus.PAUSED
            await session.flush()

            registration = await RouteExecutionService(
                session, account.id
            ).register_event(
                source_id=source.id,
                cursor_message_id=401,
                telegram_message_id=401,
            )

            assert registration.executions == ()
    finally:
        if account_id is not None:
            async with database.transaction() as session:
                await session.execute(delete(Account).where(Account.id == account_id))
        await database.close()


@pytest.mark.asyncio
async def test_final_route_execution_cannot_reenter_queue():
    database_url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL or DATABASE_URL is required")

    database = Database.from_env()
    account_id = None
    try:
        async with database.transaction() as session:
            account, source, _routes = await _seed(session, route_count=1)
            account_id = account.id
            service = RouteExecutionService(session, account.id)
            registration = await service.register_event(
                source_id=source.id,
                cursor_message_id=501,
                telegram_message_id=501,
            )
            execution = registration.executions[0]
            await service.transition(
                execution.id,
                RouteExecutionStatus.FILTERED,
                reason_code="route_filter",
            )

            with pytest.raises(RouteExecutionError, match="invalid route execution transition"):
                await service.transition(
                    execution.id,
                    RouteExecutionStatus.QUEUED,
                )
    finally:
        if account_id is not None:
            async with database.transaction() as session:
                await session.execute(delete(Account).where(Account.id == account_id))
        await database.close()
