"""Database-backed v2 smoke test used by pytest and Docker."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from sqlalchemy import text

from app.content import ContentPipeline, PipelineDecision
from app.db import Database
from app.db.config import DatabaseSettings
from app.publishing import PublishQueue
from app.telegram.v2_listener import IngestionEvent

REQUIRED_TABLES = (
    "accounts",
    "projects",
    "destinations",
    "telegram_accounts",
    "sources",
    "source_routes",
    "content_items",
    "content_fingerprints",
    "publish_jobs",
    "publication_attempts",
)


async def run_postgres_smoke(database_url: str) -> dict[str, Any]:
    """Verify schema, tenant-scoped pipeline persistence, and queue creation."""
    settings = DatabaseSettings(url=database_url)
    database = Database(settings)
    account_id = uuid.uuid4()
    project_id = uuid.uuid4()
    destination_id = uuid.uuid4()
    telegram_account_id = uuid.uuid4()
    source_id = uuid.uuid4()
    route_id = uuid.uuid4()
    try:
        if not await database.ping():
            raise AssertionError("PostgreSQL ping failed")
        async with database.session_factory() as session:
            for table in REQUIRED_TABLES:
                exists = await session.scalar(
                    text("SELECT to_regclass(:table_name)"), {"table_name": table}
                )
                if exists != table:
                    raise AssertionError(f"schema table is missing: {table}")

        async with database.session_factory() as session:
            async with session.begin():
                parameters = {
                    "id": account_id,
                    "slug": f"integration-{account_id.hex[:12]}",
                    "project_id": project_id,
                    "project_slug": f"project-{project_id.hex[:12]}",
                    "telegram_account_id": telegram_account_id,
                    "session_key": f"integration-{telegram_account_id.hex}",
                    "account_id": account_id,
                    "destination_id": destination_id,
                    "source_id": source_id,
                    "route_id": route_id,
                }
                statements = (
                    "INSERT INTO accounts (id, name, slug) "
                    "VALUES (:id, 'Integration Account', :slug)",
                    "INSERT INTO projects (id, account_id, name, slug) "
                    "VALUES (:project_id, :account_id, 'Integration Project', :project_slug)",
                    "INSERT INTO telegram_accounts (id, account_id, label, session_key) "
                    "VALUES (:telegram_account_id, :account_id, 'integration', :session_key)",
                    "INSERT INTO destinations "
                    "(id, account_id, project_id, name, telegram_chat_id, status, publishing_mode) "
                    "VALUES (:destination_id, :account_id, :project_id, 'Integration Target', "
                    "-100000001, 'active', 'direct')",
                    "INSERT INTO sources "
                    "(id, account_id, telegram_account_id, telegram_chat_id, title, status) "
                    "VALUES (:source_id, :account_id, :telegram_account_id, -100000002, "
                    "'Integration Source', 'active')",
                    "INSERT INTO source_routes "
                    "(id, account_id, source_id, destination_id, status, publishing_mode) "
                    "VALUES (:route_id, :account_id, :source_id, :destination_id, "
                    "'active', 'direct')",
                )
                for statement in statements:
                    await session.execute(text(statement), parameters)

        pipeline = ContentPipeline(database.session_factory, account_id)
        queue = PublishQueue(database.session_factory, account_id, worker_id="integration-test")
        event = IngestionEvent(
            account_id=account_id,
            source_id=source_id,
            route_id=route_id,
            destination_id=destination_id,
            chat_id=-100000002,
            message_id=9001,
            grouped_id=None,
            message=SimpleNamespace(text="رسالة تكاملية 🚨 https://t.me/source"),
            received_at=datetime.now(UTC),
        )
        result = await pipeline.process(event)
        if result.decision is not PipelineDecision.ACCEPTED:
            raise AssertionError(f"pipeline rejected integration message: {result.reason}")
        job = await queue.enqueue_result(result)
        if job is None:
            raise AssertionError("accepted content did not create a publish job")

        async with database.session_factory() as session:
            content_count = await session.scalar(
                text("SELECT count(*) FROM content_items WHERE id = :content_id"),
                {"content_id": result.content_item_id},
            )
            job_count = await session.scalar(
                text("SELECT count(*) FROM publish_jobs WHERE id = :job_id"),
                {"job_id": job.id},
            )
            if content_count != 1 or job_count != 1:
                raise AssertionError("pipeline or queue persistence check failed")

        return {
            "status": "passed",
            "account_id": str(account_id),
            "content_item_id": str(result.content_item_id),
            "publish_job_id": str(job.id),
            "decision": result.decision.value,
        }
    finally:
        async with database.session_factory() as session:
            async with session.begin():
                await session.execute(
                    text("DELETE FROM accounts WHERE id = :account_id"),
                    {"account_id": account_id},
                )
        await database.close()
