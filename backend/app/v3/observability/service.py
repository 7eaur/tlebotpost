"""Database-backed, content-free operational diagnostics for V3."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    PublicationAttempt,
    PublishedMessage,
    PublishJob,
    RouteExecution,
    SystemEvent,
)

from .contracts import AttemptDiagnostic, JobDiagnostic, RuntimeMetrics


class ObservabilityError(RuntimeError):
    """Safe operational error intended for owner-facing diagnostics."""


class ObservabilityServiceV3:
    """Query IDs/status/reason codes without loading message bodies."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        account_id: uuid.UUID,
    ) -> None:
        self.session_factory = session_factory
        self.account_id = account_id

    async def metrics(self) -> RuntimeMetrics:
        async with self.session_factory() as session:
            jobs = {
                status.value if hasattr(status, "value") else str(status): int(count)
                for status, count in (
                    await session.execute(
                        select(PublishJob.status, func.count())
                        .where(
                            PublishJob.account_id == self.account_id,
                            PublishJob.route_execution_id.is_not(None),
                        )
                        .group_by(PublishJob.status)
                    )
                ).all()
            }
            executions = {
                status.value if hasattr(status, "value") else str(status): int(count)
                for status, count in (
                    await session.execute(
                        select(RouteExecution.status, func.count())
                        .where(RouteExecution.account_id == self.account_id)
                        .group_by(RouteExecution.status)
                    )
                ).all()
            }
            attempts_total = int(
                (
                    await session.scalar(
                        select(func.count())
                        .select_from(PublicationAttempt)
                        .join(PublishJob, PublishJob.id == PublicationAttempt.publish_job_id)
                        .where(
                            PublishJob.account_id == self.account_id,
                            PublishJob.route_execution_id.is_not(None),
                        )
                    )
                )
                or 0
            )
            system_events_total = int(
                (
                    await session.scalar(
                        select(func.count())
                        .select_from(SystemEvent)
                        .where(SystemEvent.account_id == self.account_id)
                    )
                )
                or 0
            )
            return RuntimeMetrics(
                jobs_by_status=jobs,
                executions_by_status=executions,
                attempts_total=attempts_total,
                system_events_total=system_events_total,
            )

    async def diagnose_job(self, job_id: str | uuid.UUID) -> JobDiagnostic:
        identifier = _uuid(job_id, "job_id")
        async with self.session_factory() as session:
            job = await session.scalar(
                select(PublishJob).where(
                    PublishJob.id == identifier,
                    PublishJob.account_id == self.account_id,
                    PublishJob.route_execution_id.is_not(None),
                )
            )
            if job is None:
                raise ObservabilityError("publish_job_not_found")

            execution = None
            if job.route_execution_id is not None:
                execution = await session.scalar(
                    select(RouteExecution).where(
                        RouteExecution.id == job.route_execution_id,
                        RouteExecution.account_id == self.account_id,
                    )
                )
            attempts = (
                await session.scalars(
                    select(PublicationAttempt)
                    .where(PublicationAttempt.publish_job_id == job.id)
                    .order_by(PublicationAttempt.attempt_number)
                )
            ).all()
            published = await session.scalar(
                select(PublishedMessage).where(PublishedMessage.publish_job_id == job.id)
            )
            message_ids = _published_message_ids(published)
            return JobDiagnostic(
                job_id=job.id,
                job_status=job.status.value,
                route_execution_id=job.route_execution_id,
                route_status=execution.status.value if execution is not None else None,
                route_reason_code=execution.reason_code if execution is not None else None,
                destination_id=job.destination_id,
                source_route_id=job.source_route_id,
                attempt_count=job.attempt_count,
                max_attempts=job.max_attempts,
                last_error_code=job.last_error_code,
                locked_by=job.locked_by,
                scheduled_for=job.scheduled_for,
                next_attempt_at=job.next_attempt_at,
                published_at=job.published_at,
                telegram_message_ids=message_ids,
                attempts=tuple(
                    AttemptDiagnostic(
                        attempt_number=item.attempt_number,
                        status=item.status.value,
                        error_code=item.error_code,
                        telegram_message_id=item.telegram_message_id,
                        latency_ms=item.latency_ms,
                        started_at=item.started_at,
                        finished_at=item.finished_at,
                    )
                    for item in attempts
                ),
            )

    async def record_event(
        self,
        event_type: str,
        *,
        severity: str = "info",
        entity_type: str | None = None,
        entity_id: uuid.UUID | None = None,
        error_code: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        event = event_type.strip()
        if not event or len(event) > 120:
            raise ObservabilityError("event_type_invalid")
        level = severity.strip().lower()
        if level not in {"debug", "info", "warning", "error", "critical"}:
            raise ObservabilityError("event_severity_invalid")
        safe_details = _safe_details(details or {})
        async with self.session_factory() as session:
            async with session.begin():
                session.add(
                    SystemEvent(
                        account_id=self.account_id,
                        event_type=event,
                        severity=level,
                        entity_type=entity_type[:120] if entity_type else None,
                        entity_id=entity_id,
                        error_code=error_code[:120] if error_code else None,
                        details=safe_details,
                    )
                )


_BLOCKED_DETAIL_KEYS = (
    "token",
    "secret",
    "password",
    "api_hash",
    "session",
    "content",
    "caption",
    "message_text",
    "text_original",
    "text_normalized",
)


def _safe_details(details: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for raw_key, value in details.items():
        key = str(raw_key)
        lowered = key.lower()
        if any(blocked in lowered for blocked in _BLOCKED_DETAIL_KEYS):
            raise ObservabilityError("unsafe_event_detail_key")
        if isinstance(value, (str, int, float, bool)) or value is None:
            result[key] = value
        elif isinstance(value, uuid.UUID):
            result[key] = str(value)
        else:
            raise ObservabilityError("unsafe_event_detail_value")
    return result


def _published_message_ids(published: PublishedMessage | None) -> tuple[int, ...]:
    if published is None:
        return ()
    metadata = published.metadata_json if isinstance(published.metadata_json, dict) else {}
    raw = metadata.get("message_ids")
    if isinstance(raw, list):
        values = tuple(int(value) for value in raw if isinstance(value, int) and value > 0)
        if values:
            return values
    return (int(published.telegram_message_id),)


def _uuid(value: str | uuid.UUID, field: str) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(value.strip())
    except (AttributeError, ValueError) as exc:
        raise ObservabilityError(f"{field}_invalid") from exc
