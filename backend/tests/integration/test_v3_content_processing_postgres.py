from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select

from app.db import Database
from app.db.models import (
    Account,
    BrandingProfile,
    Destination,
    DestinationStatus,
    FilterProfile,
    Project,
    RouteExecution,
    RouteExecutionStatus,
    RouteStatus,
    Source,
    SourceCheckpoint,
    SourceRoute,
    TelegramAccount,
    TransformProfile,
)
from app.v3.content import ContentProcessingCoordinator, ProcessingDecision
from app.v3.domain import RouteExecutionService
from app.v3.telegram.types import SourceEvent

pytestmark = pytest.mark.integration


def message(message_id: int, text: str):
    return SimpleNamespace(
        id=message_id,
        grouped_id=None,
        date=datetime(2026, 10, 8, 0, 30, tzinfo=UTC),
        raw_text=text,
        media=None,
    )


async def _seed_base(session):
    account = Account(name="V3 Content Test", slug=f"v3-content-{uuid.uuid4().hex[:10]}")
    session.add(account)
    await session.flush()

    project = Project(
        account_id=account.id,
        name="Content Project",
        slug=f"content-{uuid.uuid4().hex[:10]}",
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
        telegram_chat_id=-1003000000001,
        title="Content Source",
    )
    session.add(source)
    await session.flush()
    return account, project, source


async def _destination(session, account, project, *, index: int, branding_profile_id=None):
    destination = Destination(
        account_id=account.id,
        project_id=project.id,
        name=f"Destination {index}",
        telegram_chat_id=-1004000000000 - index,
        status=DestinationStatus.ACTIVE,
        branding_profile_id=branding_profile_id,
    )
    session.add(destination)
    await session.flush()
    return destination


@pytest.mark.asyncio
async def test_route_profiles_are_isolated_and_route_branding_overrides_destination():
    database_url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL or DATABASE_URL is required")

    database = Database.from_env()
    account_id = None
    try:
        async with database.transaction() as session:
            account, project, source = await _seed_base(session)
            account_id = account.id

            route_brand = BrandingProfile(
                account_id=account.id,
                name="Route Brand",
                footer="علامة المسار",
                separator="———",
                enabled=True,
            )
            destination_brand = BrandingProfile(
                account_id=account.id,
                name="Destination Brand",
                footer="علامة الوجهة",
                enabled=True,
            )
            route_filter = FilterProfile(
                account_id=account.id,
                name="Route Filter",
                include_keywords=["عاجل"],
                exclude_keywords=[],
                allowed_media_types=[],
                remove_urls=True,
                remove_source_rights=True,
                preserve_emoji=False,
                custom_rules={},
            )
            compact = TransformProfile(
                account_id=account.id,
                name="Compact",
                preserve_line_breaks=True,
                trim_whitespace=True,
                options={"max_blank_lines": 1},
            )
            session.add_all([route_brand, destination_brand, route_filter, compact])
            await session.flush()

            first_destination = await _destination(session, account, project, index=1)
            second_destination = await _destination(
                session,
                account,
                project,
                index=2,
                branding_profile_id=destination_brand.id,
            )
            first_route = SourceRoute(
                account_id=account.id,
                source_id=source.id,
                destination_id=first_destination.id,
                status=RouteStatus.ACTIVE,
                filter_profile_id=route_filter.id,
                transform_profile_id=compact.id,
                branding_profile_id=route_brand.id,
            )
            second_route = SourceRoute(
                account_id=account.id,
                source_id=source.id,
                destination_id=second_destination.id,
                status=RouteStatus.ACTIVE,
            )
            session.add_all([first_route, second_route])
            await session.flush()

            registration = await RouteExecutionService(session, account.id).register_event(
                source_id=source.id,
                cursor_message_id=701,
                telegram_message_id=701,
            )

        event = SourceEvent.from_messages(
            account_id=account_id,
            source_id=source.id,
            chat_id=source.telegram_chat_id,
            messages=(message(701, "عاجل ✅ خبر https://example.com/story"),),
        )
        results = await ContentProcessingCoordinator(
            database.session_factory,
            account_id,
        ).process_registration(event, registration)

        assert len(results) == 2
        assert all(item.decision is ProcessingDecision.READY_FOR_DEDUP for item in results)
        rendered = {item.execution_id: item.content for item in results}
        first = next(item for item in registration.executions if item.route_id == first_route.id)
        second = next(item for item in registration.executions if item.route_id == second_route.id)
        assert rendered[first.id] is not None
        assert rendered[second.id] is not None
        assert rendered[first.id].normalized_text == "عاجل  خبر"
        assert rendered[first.id].rendered_text.endswith("علامة المسار")
        assert "✅" in rendered[second.id].normalized_text
        assert rendered[second.id].rendered_text.endswith("علامة الوجهة")
        assert "example.com" not in rendered[first.id].normalized_text
        assert "example.com" not in rendered[second.id].normalized_text

        async with database.session() as session:
            executions = (
                await session.scalars(
                    select(RouteExecution)
                    .where(RouteExecution.account_id == account_id)
                    .order_by(RouteExecution.route_id)
                )
            ).all()
            assert {item.status for item in executions} == {
                RouteExecutionStatus.READY_FOR_DEDUP
            }
            assert {item.reason_code for item in executions} == {"ready_for_dedup"}
    finally:
        if account_id is not None:
            async with database.transaction() as session:
                await session.execute(delete(Account).where(Account.id == account_id))
        await database.close()


@pytest.mark.asyncio
async def test_all_filtered_routes_allow_committed_checkpoint_to_advance():
    database_url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL or DATABASE_URL is required")

    database = Database.from_env()
    account_id = None
    source_id = None
    try:
        async with database.transaction() as session:
            account, project, source = await _seed_base(session)
            account_id = account.id
            source_id = source.id
            required = FilterProfile(
                account_id=account.id,
                name="Economy only",
                include_keywords=["اقتصاد"],
                exclude_keywords=[],
                allowed_media_types=[],
                remove_urls=True,
                remove_source_rights=True,
                preserve_emoji=True,
                custom_rules={},
            )
            session.add(required)
            await session.flush()

            for index in (1, 2):
                destination = await _destination(session, account, project, index=10 + index)
                session.add(
                    SourceRoute(
                        account_id=account.id,
                        source_id=source.id,
                        destination_id=destination.id,
                        status=RouteStatus.ACTIVE,
                        filter_profile_id=required.id,
                    )
                )
            session.add(
                SourceCheckpoint(
                    source_id=source.id,
                    last_seen_message_id=801,
                    last_committed_message_id=0,
                )
            )
            await session.flush()
            registration = await RouteExecutionService(session, account.id).register_event(
                source_id=source.id,
                cursor_message_id=801,
                telegram_message_id=801,
            )

        event = SourceEvent.from_messages(
            account_id=account_id,
            source_id=source_id,
            chat_id=-1003000000001,
            messages=(message(801, "خبر رياضي"),),
        )
        results = await ContentProcessingCoordinator(
            database.session_factory,
            account_id,
        ).process_registration(event, registration)

        assert [item.decision for item in results] == [
            ProcessingDecision.FILTERED,
            ProcessingDecision.FILTERED,
        ]
        assert {item.reason_code for item in results} == {"missing_include_keyword"}

        async with database.session() as session:
            executions = (
                await session.scalars(
                    select(RouteExecution).where(RouteExecution.account_id == account_id)
                )
            ).all()
            assert {item.status for item in executions} == {RouteExecutionStatus.FILTERED}
            checkpoint = await session.get(SourceCheckpoint, source_id)
            assert checkpoint is not None
            assert checkpoint.last_seen_message_id == 801
            assert checkpoint.last_committed_message_id == 801
    finally:
        if account_id is not None:
            async with database.transaction() as session:
                await session.execute(delete(Account).where(Account.id == account_id))
        await database.close()
