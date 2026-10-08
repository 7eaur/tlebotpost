from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select

from app.db import Database
from app.db.models import (
    Account,
    AttemptStatus,
    ContentType,
    Destination,
    JobStatus,
    Project,
    PublicationAttempt,
    PublishedMessage,
    PublishJob,
    RouteExecution,
    RouteExecutionStatus,
    RoutePublishPayload,
    Source,
    SourceRoute,
    SystemEvent,
    TelegramAccount,
)
from app.v3.observability import ObservabilityError, ObservabilityServiceV3

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_observability_diagnoses_failed_job_without_loading_content_or_error_messages():
    if not (os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")):
        pytest.skip("TEST_DATABASE_URL or DATABASE_URL is required")

    database = Database.from_env()
    account_id = None
    now = datetime(2026, 10, 9, 3, 0, tzinfo=UTC)
    secret_body = "SECRET MESSAGE BODY MUST NEVER APPEAR"
    secret_error = "SECRET EXTERNAL ERROR DETAIL MUST NEVER APPEAR"

    try:
        async with database.transaction() as session:
            account = Account(
                name="V3 Observability",
                slug=f"obs-{uuid.uuid4().hex[:10]}",
            )
            session.add(account)
            await session.flush()
            account_id = account.id

            project = Project(
                account_id=account.id,
                name="Observability Project",
                slug=f"obs-project-{uuid.uuid4().hex[:8]}",
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
                telegram_chat_id=-1004111111111,
                title="Source",
            )
            destination = Destination(
                account_id=account.id,
                project_id=project.id,
                name="Target",
                telegram_chat_id=-1004222222222,
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
                event_key="message:9901",
                cursor_message_id=9901,
                telegram_message_id=9901,
                status=RouteExecutionStatus.FAILED,
                reason_code="telegram_forbidden",
                terminal_at=now,
            )
            session.add(execution)
            await session.flush()

            payload = RoutePublishPayload(
                account_id=account.id,
                route_execution_id=execution.id,
                content_type=ContentType.TEXT,
                normalized_text=secret_body,
                rendered_text=secret_body,
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
                status=JobStatus.FAILED,
                scheduled_for=now,
                attempt_count=1,
                max_attempts=3,
                last_error_code="telegram_forbidden",
                last_error_message=secret_error,
            )
            session.add(job)
            await session.flush()
            execution.publish_job_id = job.id

            attempt = PublicationAttempt(
                publish_job_id=job.id,
                attempt_number=1,
                status=AttemptStatus.FAILED,
                telegram_message_id=88001,
                error_code="telegram_forbidden",
                error_message=secret_error,
                started_at=now,
                finished_at=now,
                latency_ms=120,
            )
            published = PublishedMessage(
                publish_job_id=job.id,
                destination_id=destination.id,
                telegram_message_id=88001,
                published_at=now,
                metadata_json={
                    "message_ids": [88001, 88002],
                    "message_count": 2,
                    "partial": True,
                    "v3": True,
                },
            )
            session.add_all([attempt, published])
            await session.flush()
            job_id = job.id

        service = ObservabilityServiceV3(database.session_factory, account_id)
        diagnostic = await service.diagnose_job(job_id)

        assert diagnostic.job_id == job_id
        assert diagnostic.job_status == "failed"
        assert diagnostic.route_status == "failed"
        assert diagnostic.route_reason_code == "telegram_forbidden"
        assert diagnostic.last_error_code == "telegram_forbidden"
        assert diagnostic.telegram_message_ids == (88001, 88002)
        assert len(diagnostic.attempts) == 1
        assert diagnostic.attempts[0].error_code == "telegram_forbidden"
        assert diagnostic.attempts[0].telegram_message_id == 88001

        rendered = repr(diagnostic)
        assert secret_body not in rendered
        assert secret_error not in rendered

        metrics = await service.metrics()
        assert metrics.jobs_by_status["failed"] == 1
        assert metrics.executions_by_status["failed"] == 1
        assert metrics.attempts_total == 1
        assert metrics.system_events_total == 0

        await service.record_event(
            "runtime_ready",
            details={
                "state": "ready",
                "components_started": 5,
                "job_id": job_id,
            },
        )
        after = await service.metrics()
        assert after.system_events_total == 1

        async with database.session() as session:
            event = await session.scalar(
                select(SystemEvent).where(SystemEvent.account_id == account_id)
            )
            assert event is not None
            assert event.event_type == "runtime_ready"
            assert event.details["job_id"] == str(job_id)
            assert secret_body not in repr(event.details)
            assert secret_error not in repr(event.details)

        with pytest.raises(ObservabilityError, match="unsafe_event_detail_key"):
            await service.record_event(
                "unsafe",
                details={"message_text": secret_body},
            )
        with pytest.raises(ObservabilityError, match="unsafe_event_detail_value"):
            await service.record_event(
                "unsafe",
                details={"payload": {"nested": secret_body}},
            )
    finally:
        if account_id is not None:
            async with database.transaction() as session:
                await session.execute(delete(Account).where(Account.id == account_id))
        await database.close()
