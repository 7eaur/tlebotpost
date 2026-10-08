from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete

from app.db import Database
from app.db.models import (
    Account,
    ContentType,
    Destination,
    JobStatus,
    Project,
    ProjectStatus,
    PublishJob,
    RouteExecution,
    RouteExecutionStatus,
    RoutePublishPayload,
    Source,
    SourceCheckpoint,
    SourceRoute,
    TelegramAccount,
)
from app.telegram.chat_resolver import ResolvedChat
from app.v3.control import ControlServiceV3
from app.v3.publish_queue import PublishQueueV3

pytestmark = pytest.mark.integration


async def seed_account(session):
    account = Account(name="V3 Control", slug=f"control-{uuid.uuid4().hex[:10]}")
    session.add(account)
    await session.flush()

    project = Project(
        account_id=account.id,
        name="Main Project",
        slug="main",
        status=ProjectStatus.ACTIVE,
    )
    telegram = TelegramAccount(
        account_id=account.id,
        label=f"telegram-{uuid.uuid4().hex[:8]}",
        session_key=f"session-{uuid.uuid4()}",
    )
    session.add_all([project, telegram])
    await session.flush()
    return account, project, telegram


def resolver():
    async def resolve(input_ref: str) -> ResolvedChat:
        if input_ref == "@source":
            return ResolvedChat(
                chat_id=-1001111111111,
                input_ref="@source",
                title="Source Channel",
                username="source",
                latest_message_id=777,
            )
        if input_ref == "@target":
            return ResolvedChat(
                chat_id=-1002222222222,
                input_ref="@target",
                title="Target Channel",
                username="target",
                latest_message_id=55,
            )
        raise ValueError("unknown test chat")

    return resolve


@pytest.mark.asyncio
async def test_control_service_manages_source_destination_route_and_project_without_db_edits():
    if not (os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")):
        pytest.skip("TEST_DATABASE_URL or DATABASE_URL is required")

    database = Database.from_env()
    account_id = None
    reloads: list[str] = []

    async def reload_callback() -> None:
        reloads.append("reload")

    try:
        async with database.transaction() as session:
            account, project, telegram = await seed_account(session)
            account_id = account.id
            project_id = project.id
            telegram_id = telegram.id

        queue = PublishQueueV3(database.session_factory, account_id)
        service = ControlServiceV3(
            session_factory=database.session_factory,
            account_id=account_id,
            telegram_account_id=telegram_id,
            resolve_chat=resolver(),
            reload_callback=reload_callback,
            queue=queue,
            telegram_connected=lambda: True,
        )

        source = await service.add_source("@source")
        assert source.title == "Source Channel"
        assert source.status == "active"
        assert source.last_seen_message_id == 777

        async with database.session() as session:
            checkpoint = await session.get(SourceCheckpoint, source.id)
            assert checkpoint is not None
            assert checkpoint.last_seen_message_id == 777
            assert checkpoint.last_committed_message_id == 0

        destination = await service.add_destination("@target")
        assert destination.project_id == project_id
        assert destination.status == "active"

        route = await service.add_route(str(source.id), str(destination.id))
        assert route.status == "active"
        assert route.source_id == source.id
        assert route.destination_id == destination.id

        assert len(reloads) == 3

        status = await service.status()
        assert status.telegram_connected is True
        assert status.projects_active == 1
        assert status.sources_active == 1
        assert status.destinations_active == 1
        assert status.routes_active == 1

        paused = await service.set_project_active(project_selector=None, active=False)
        assert paused.status == "paused"

        resumed = await service.set_project_active(project_selector=None, active=True)
        assert resumed.status == "active"

        source_off = await service.set_source_active(str(source.id), active=False)
        assert source_off.status == "paused"
        source_on = await service.set_source_active(str(source.id), active=True)
        assert source_on.status == "active"

        destination_off = await service.set_destination_active(
            str(destination.id),
            active=False,
        )
        assert destination_off.status == "paused"
        destination_on = await service.set_destination_active(
            str(destination.id),
            active=True,
        )
        assert destination_on.status == "active"

        route_off = await service.set_route_active(str(route.id), active=False)
        assert route_off.status == "paused"
        route_on = await service.set_route_active(str(route.id), active=True)
        assert route_on.status == "active"

        assert len(reloads) == 9

        projects = await service.list_projects()
        sources = await service.list_sources()
        destinations = await service.list_destinations()
        routes = await service.list_routes()
        assert [item.id for item in projects] == [project_id]
        assert [item.id for item in sources] == [source.id]
        assert [item.id for item in destinations] == [destination.id]
        assert [item.id for item in routes] == [route.id]
    finally:
        if account_id is not None:
            async with database.transaction() as session:
                await session.execute(delete(Account).where(Account.id == account_id))
        await database.close()


@pytest.mark.asyncio
async def test_control_service_releases_manual_job_and_reports_queue_health():
    if not (os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")):
        pytest.skip("TEST_DATABASE_URL or DATABASE_URL is required")

    database = Database.from_env()
    account_id = None
    now = datetime(2026, 10, 9, 2, 0, tzinfo=UTC)

    async def no_reload() -> None:
        return None

    try:
        async with database.transaction() as session:
            account, project, telegram = await seed_account(session)
            account_id = account.id

            source = Source(
                account_id=account.id,
                telegram_account_id=telegram.id,
                telegram_chat_id=-1003111111111,
                title="Source",
            )
            destination = Destination(
                account_id=account.id,
                project_id=project.id,
                name="Target",
                telegram_chat_id=-1003222222222,
            )
            session.add_all([source, destination])
            await session.flush()

            route = SourceRoute(
                account_id=account.id,
                source_id=source.id,
                destination_id=destination.id,
            )
            session.add(route)
            await session.flush()

            execution = RouteExecution(
                account_id=account.id,
                source_id=source.id,
                route_id=route.id,
                destination_id=destination.id,
                event_key="message:9001",
                cursor_message_id=9001,
                telegram_message_id=9001,
                status=RouteExecutionStatus.QUEUED,
            )
            session.add(execution)
            await session.flush()

            payload = RoutePublishPayload(
                account_id=account.id,
                route_execution_id=execution.id,
                content_type=ContentType.TEXT,
                normalized_text="manual",
                rendered_text="manual",
                media_json=[],
            )
            session.add(payload)
            await session.flush()

            job = PublishJob(
                account_id=account.id,
                content_item_id=None,
                route_execution_id=execution.id,
                route_payload_id=payload.id,
                destination_id=destination.id,
                source_route_id=route.id,
                status=JobStatus.MANUAL_HOLD,
                scheduled_for=now,
                max_attempts=3,
            )
            session.add(job)
            await session.flush()
            execution.publish_job_id = job.id
            job_id = job.id
            telegram_id = telegram.id

        queue = PublishQueueV3(database.session_factory, account_id)
        service = ControlServiceV3(
            session_factory=database.session_factory,
            account_id=account_id,
            telegram_account_id=telegram_id,
            resolve_chat=resolver(),
            reload_callback=no_reload,
            queue=queue,
        )

        before = await service.status()
        assert before.queue_pending == 1

        released = await service.release_manual_job(str(job_id))
        assert released == job_id

        async with database.session() as session:
            stored = await session.get(PublishJob, job_id)
            assert stored is not None
            assert stored.status is JobStatus.QUEUED
    finally:
        if account_id is not None:
            async with database.transaction() as session:
                await session.execute(delete(Account).where(Account.id == account_id))
        await database.close()
