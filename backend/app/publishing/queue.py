"""Transactional publish queue for direct and scheduled jobs."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.content import PipelineDecision, PipelineResult
from app.db.models import (
    AttemptStatus,
    ContentItem,
    ContentStatus,
    Destination,
    JobStatus,
    PublicationAttempt,
    PublishedMessage,
    PublishJob,
    ScheduleProfile,
    SourceRoute,
    SystemEvent,
)

from .scheduler import next_run_at


class QueueError(RuntimeError):
    """Raised for invalid queue operations."""


class PublishQueue:
    """Create, claim, and transition publish jobs within account boundaries."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        account_id: uuid.UUID,
        *,
        worker_id: str,
    ) -> None:
        self.session_factory = session_factory
        self.account_id = account_id
        self.worker_id = worker_id

    async def enqueue_result(
        self,
        result: PipelineResult,
        *,
        scheduled_for: datetime | None = None,
        priority: int = 100,
    ) -> PublishJob | None:
        if result.decision is not PipelineDecision.ACCEPTED:
            return None
        if result.content_item_id is None:
            raise QueueError("accepted result must contain content_item_id")
        if priority < 0:
            raise QueueError("priority must be non-negative")

        event = result.event
        now = datetime.now(UTC)
        async with self.session_factory() as session:
            async with session.begin():
                destination = await session.scalar(
                    select(Destination).where(
                        Destination.id == event.destination_id,
                        Destination.account_id == self.account_id,
                    )
                )
                if destination is None:
                    raise QueueError("destination is not available in this account")
                route = await session.scalar(
                    select(SourceRoute).where(
                        SourceRoute.id == event.route_id,
                        SourceRoute.account_id == self.account_id,
                    )
                )
                schedule_id = (
                    route.schedule_profile_id
                    if route is not None and route.schedule_profile_id is not None
                    else destination.schedule_profile_id
                )
                schedule = None
                if schedule_id is not None:
                    schedule = await session.scalar(
                        select(ScheduleProfile).where(
                            ScheduleProfile.id == schedule_id,
                            ScheduleProfile.account_id == self.account_id,
                        )
                    )
                execution_time = scheduled_for or self._scheduled_time(
                    destination, schedule, now
                )
                existing = await session.scalar(
                    select(PublishJob).where(
                        PublishJob.account_id == self.account_id,
                        PublishJob.content_item_id == result.content_item_id,
                        PublishJob.destination_id == event.destination_id,
                    )
                )
                if existing is not None:
                    return existing
                job = PublishJob(
                    account_id=self.account_id,
                    content_item_id=result.content_item_id,
                    destination_id=event.destination_id,
                    source_route_id=event.route_id,
                    status=JobStatus.QUEUED,
                    scheduled_for=execution_time,
                    priority=priority,
                )
                session.add(job)
                item = await session.get(ContentItem, result.content_item_id)
                if item is not None:
                    item.status = ContentStatus.QUEUED
                await session.flush()
                return job

    async def claim_due(
        self,
        *,
        limit: int = 10,
        now: datetime | None = None,
        lock_timeout_seconds: int = 900,
    ) -> list[PublishJob]:
        if limit <= 0:
            raise QueueError("limit must be positive")
        current = now or datetime.now(UTC)
        if current.tzinfo is None:
            current = current.replace(tzinfo=UTC)
        stale_before = current - timedelta(seconds=lock_timeout_seconds)
        async with self.session_factory() as session:
            async with session.begin():
                statement = (
                    select(PublishJob)
                    .where(
                        PublishJob.account_id == self.account_id,
                        or_(
                            PublishJob.status == JobStatus.QUEUED,
                            (
                                (PublishJob.status == JobStatus.RETRY_WAIT)
                                & (
                                    PublishJob.next_attempt_at.is_(None)
                                    | (PublishJob.next_attempt_at <= current)
                                )
                            ),
                            (
                                (PublishJob.status == JobStatus.PROCESSING)
                                & (PublishJob.locked_at <= stale_before)
                            ),
                        ),
                        PublishJob.scheduled_for <= current,
                    )
                    .order_by(
                        PublishJob.priority,
                        PublishJob.scheduled_for,
                        PublishJob.created_at,
                    )
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
                jobs = list((await session.scalars(statement)).all())
                for job in jobs:
                    job.status = JobStatus.PROCESSING
                    job.locked_at = current
                    job.locked_by = self.worker_id
                    job.attempt_count += 1
                    item = await session.get(ContentItem, job.content_item_id)
                    if item is not None:
                        item.status = ContentStatus.PROCESSING
                    session.add(
                        PublicationAttempt(
                            publish_job_id=job.id,
                            attempt_number=job.attempt_count,
                            status=AttemptStatus.STARTED,
                            started_at=current,
                        )
                    )
                await session.flush()
                return jobs

    async def mark_published(
        self,
        job_id: uuid.UUID,
        *,
        telegram_message_id: int,
        finished_at: datetime | None = None,
        latency_ms: int | None = None,
    ) -> None:
        current = finished_at or datetime.now(UTC)
        async with self.session_factory() as session:
            async with session.begin():
                job = await self._owned_job(session, job_id)
                job.status = JobStatus.PUBLISHED
                job.published_at = current
                job.next_attempt_at = None
                job.locked_at = None
                job.locked_by = None
                job.last_error_code = None
                job.last_error_message = None

                item = await session.get(ContentItem, job.content_item_id)
                if item is not None:
                    item.status = ContentStatus.PUBLISHED
                existing = await session.scalar(
                    select(PublishedMessage).where(PublishedMessage.publish_job_id == job.id)
                )
                if existing is None:
                    session.add(
                        PublishedMessage(
                            publish_job_id=job.id,
                            destination_id=job.destination_id,
                            telegram_message_id=telegram_message_id,
                            published_at=current,
                            metadata_json={"worker_id": self.worker_id},
                        )
                    )
                await self._finish_attempt(
                    session,
                    job,
                    AttemptStatus.SUCCEEDED,
                    telegram_message_id=telegram_message_id,
                    finished_at=current,
                    latency_ms=latency_ms,
                )

    async def mark_retry(
        self,
        job_id: uuid.UUID,
        *,
        error_code: str,
        error_message: str,
        retry_at: datetime,
    ) -> None:
        async with self.session_factory() as session:
            async with session.begin():
                job = await self._owned_job(session, job_id)
                job.status = JobStatus.RETRY_WAIT
                job.next_attempt_at = retry_at
                job.last_error_code = error_code
                job.last_error_message = error_message[:4000]
                job.locked_at = None
                job.locked_by = None
                item = await session.get(ContentItem, job.content_item_id)
                if item is not None:
                    item.status = ContentStatus.QUEUED
                await self._finish_attempt(
                    session,
                    job,
                    AttemptStatus.RETRYING,
                    error_code=error_code,
                    error_message=error_message,
                    finished_at=datetime.now(UTC),
                )
                session.add(
                    SystemEvent(
                        account_id=self.account_id,
                        event_type="publish_retry",
                        severity="warning",
                        entity_type="publish_job",
                        entity_id=job.id,
                        error_code=error_code,
                        details={"attempt": job.attempt_count, "retry_at": retry_at.isoformat()},
                    )
                )

    async def mark_failed(
        self,
        job_id: uuid.UUID,
        *,
        error_code: str,
        error_message: str,
    ) -> None:
        async with self.session_factory() as session:
            async with session.begin():
                job = await self._owned_job(session, job_id)
                job.status = JobStatus.FAILED
                job.last_error_code = error_code
                job.last_error_message = error_message[:4000]
                job.locked_at = None
                job.locked_by = None
                item = await session.get(ContentItem, job.content_item_id)
                if item is not None:
                    item.status = ContentStatus.FAILED
                await self._finish_attempt(
                    session,
                    job,
                    AttemptStatus.FAILED,
                    error_code=error_code,
                    error_message=error_message,
                    finished_at=datetime.now(UTC),
                )
                session.add(
                    SystemEvent(
                        account_id=self.account_id,
                        event_type="publish_failed",
                        severity="error",
                        entity_type="publish_job",
                        entity_id=job.id,
                        error_code=error_code,
                        details={"attempt": job.attempt_count, "message": error_message[:500]},
                    )
                )

    async def requeue(self, job_id: uuid.UUID) -> PublishJob:
        async with self.session_factory() as session:
            async with session.begin():
                job = await self._account_job(session, job_id)
                if job.status == JobStatus.PUBLISHED:
                    raise QueueError("published jobs cannot be requeued")
                job.status = JobStatus.QUEUED
                job.next_attempt_at = None
                job.locked_at = None
                job.locked_by = None
                job.last_error_code = None
                job.last_error_message = None
                item = await session.get(ContentItem, job.content_item_id)
                if item is not None:
                    item.status = ContentStatus.QUEUED
                await session.flush()
                return job

    async def cancel(self, job_id: uuid.UUID) -> PublishJob:
        async with self.session_factory() as session:
            async with session.begin():
                job = await self._account_job(session, job_id)
                if job.status == JobStatus.PUBLISHED:
                    raise QueueError("published jobs cannot be cancelled")
                job.status = JobStatus.CANCELLED
                job.cancelled_at = datetime.now(UTC)
                job.locked_at = None
                job.locked_by = None
                item = await session.get(ContentItem, job.content_item_id)
                if item is not None:
                    item.status = ContentStatus.SKIPPED
                await session.flush()
                return job

    async def _account_job(self, session: AsyncSession, job_id: uuid.UUID) -> PublishJob:
        job = await session.scalar(
            select(PublishJob).where(
                PublishJob.id == job_id,
                PublishJob.account_id == self.account_id,
            )
        )
        if job is None:
            raise QueueError("publish job is not available in this account")
        return job

    async def _owned_job(self, session: AsyncSession, job_id: uuid.UUID) -> PublishJob:
        job = await self._account_job(session, job_id)
        if job.locked_by not in {None, self.worker_id}:
            raise QueueError("publish job is locked by another worker")
        return job

    @staticmethod
    def _scheduled_time(
        destination: Destination,
        schedule: ScheduleProfile | None,
        now: datetime,
    ) -> datetime:
        if destination.publishing_mode is None or destination.publishing_mode.value == "direct":
            return now
        return next_run_at(schedule, now)

    @staticmethod
    async def _finish_attempt(
        session: AsyncSession,
        job: PublishJob,
        status: AttemptStatus,
        *,
        telegram_message_id: int | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        finished_at: datetime,
        latency_ms: int | None = None,
    ) -> None:
        attempt = await session.scalar(
            select(PublicationAttempt).where(
                PublicationAttempt.publish_job_id == job.id,
                PublicationAttempt.attempt_number == job.attempt_count,
            )
        )
        if attempt is None:
            raise QueueError("publication attempt is missing")
        attempt.status = status
        attempt.telegram_message_id = telegram_message_id
        attempt.error_code = error_code
        attempt.error_message = error_message[:4000] if error_message else None
        attempt.finished_at = finished_at
        attempt.latency_ms = latency_ms
        session.add(attempt)
