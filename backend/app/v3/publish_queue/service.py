"""Durable V3 publish queue, leasing, retry, and recovery semantics."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    AttemptStatus,
    Destination,
    JobStatus,
    PublicationAttempt,
    PublishingMode,
    PublishJob,
    RouteExecution,
    RouteExecutionStatus,
    RoutePublishPayload,
    ScheduleProfile,
    SourceRoute,
)
from app.publishing.scheduler import next_run_at
from app.v3.deduplication import DeduplicationDecision, DeduplicationResult
from app.v3.domain import RouteExecutionService, SourceCheckpointCoordinator

from .contracts import QueueHandoffDecision, QueueHandoffResult, retry_delay_seconds


class QueueError(RuntimeError):
    """Raised when a V3 queue operation violates a durability contract."""


class PublishQueueV3:
    """Own V3 durable enqueue, claims, leases, retries, and stale-work recovery."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        account_id: uuid.UUID,
        *,
        default_max_attempts: int = 5,
        default_lease_seconds: int = 120,
        retry_base_seconds: int = 30,
        retry_cap_seconds: int = 3600,
    ) -> None:
        if default_max_attempts <= 0:
            raise ValueError("default_max_attempts must be positive")
        if default_lease_seconds <= 0:
            raise ValueError("default_lease_seconds must be positive")
        if retry_base_seconds <= 0:
            raise ValueError("retry_base_seconds must be positive")
        if retry_cap_seconds < retry_base_seconds:
            raise ValueError("retry_cap_seconds must be at least retry_base_seconds")
        self.session_factory = session_factory
        self.account_id = account_id
        self.default_max_attempts = default_max_attempts
        self.default_lease_seconds = default_lease_seconds
        self.retry_base_seconds = retry_base_seconds
        self.retry_cap_seconds = retry_cap_seconds
        self._logger = logging.getLogger(__name__)

    async def enqueue_result(
        self,
        result: DeduplicationResult,
        *,
        now: datetime | None = None,
    ) -> QueueHandoffResult | None:
        if result.decision is not DeduplicationDecision.READY_FOR_QUEUE:
            return None
        return await self._enqueue_execution(result.execution_id, _utc(now))

    async def recover_pending(
        self,
        *,
        limit: int = 100,
        now: datetime | None = None,
    ) -> int:
        if limit <= 0:
            raise QueueError("limit must be positive")
        async with self.session_factory() as session:
            execution_ids = list(
                (
                    await session.scalars(
                        select(RouteExecution.id)
                        .where(
                            RouteExecution.account_id == self.account_id,
                            RouteExecution.status == RouteExecutionStatus.READY_FOR_QUEUE,
                        )
                        .order_by(RouteExecution.created_at, RouteExecution.id)
                        .limit(limit)
                    )
                ).all()
            )

        recovered = 0
        current = _utc(now)
        for execution_id in execution_ids:
            try:
                await self._enqueue_execution(execution_id, current)
            except Exception:
                self._logger.exception(
                    "V3 queue handoff recovery failed: execution_id=%s",
                    execution_id,
                )
            else:
                recovered += 1
        return recovered

    async def _enqueue_execution(
        self,
        execution_id: uuid.UUID,
        current: datetime,
    ) -> QueueHandoffResult:
        async with self.session_factory() as session:
            async with session.begin():
                execution = await session.scalar(
                    select(RouteExecution)
                    .where(
                        RouteExecution.id == execution_id,
                        RouteExecution.account_id == self.account_id,
                    )
                    .with_for_update()
                )
                if execution is None:
                    raise QueueError("route_execution_unavailable")

                existing = await session.scalar(
                    select(PublishJob).where(
                        PublishJob.account_id == self.account_id,
                        PublishJob.route_execution_id == execution.id,
                    )
                )
                if existing is not None:
                    if execution.status is RouteExecutionStatus.READY_FOR_QUEUE:
                        await self._mark_execution_queued(
                            session,
                            execution,
                            existing,
                            reason_code="queue_recovered_existing_job",
                            current=current,
                        )
                    return QueueHandoffResult(
                        execution_id=execution.id,
                        job_id=existing.id,
                        decision=QueueHandoffDecision.ALREADY_ENQUEUED,
                        job_status=existing.status,
                        scheduled_for=existing.scheduled_for,
                    )

                if execution.status is not RouteExecutionStatus.READY_FOR_QUEUE:
                    raise QueueError("route_execution_not_ready_for_queue")

                payload = await session.scalar(
                    select(RoutePublishPayload).where(
                        RoutePublishPayload.account_id == self.account_id,
                        RoutePublishPayload.route_execution_id == execution.id,
                    )
                )
                if payload is None:
                    raise QueueError("route_publish_payload_missing")

                route = await session.scalar(
                    select(SourceRoute).where(
                        SourceRoute.id == execution.route_id,
                        SourceRoute.account_id == self.account_id,
                    )
                )
                destination = await session.scalar(
                    select(Destination).where(
                        Destination.id == execution.destination_id,
                        Destination.account_id == self.account_id,
                    )
                )
                if route is None or destination is None:
                    raise QueueError("queue_route_or_destination_unavailable")

                mode = route.publishing_mode or destination.publishing_mode
                schedule_id = route.schedule_profile_id or destination.schedule_profile_id
                schedule = None
                if schedule_id is not None:
                    schedule = await session.scalar(
                        select(ScheduleProfile).where(
                            ScheduleProfile.id == schedule_id,
                            ScheduleProfile.account_id == self.account_id,
                        )
                    )
                    if schedule is None:
                        raise QueueError("queue_schedule_profile_unavailable")

                job_status, scheduled_for, decision = self._initial_state(
                    mode=mode,
                    schedule=schedule,
                    current=current,
                )
                max_attempts = _max_attempts(
                    route.settings,
                    destination.settings,
                    self.default_max_attempts,
                )
                job = PublishJob(
                    account_id=self.account_id,
                    content_item_id=None,
                    route_execution_id=execution.id,
                    route_payload_id=payload.id,
                    destination_id=execution.destination_id,
                    source_route_id=execution.route_id,
                    status=job_status,
                    scheduled_for=scheduled_for,
                    priority=max(0, int(route.priority)),
                    max_attempts=max_attempts,
                )
                session.add(job)
                await session.flush()

                await self._mark_execution_queued(
                    session,
                    execution,
                    job,
                    reason_code=(
                        "queue_manual_hold"
                        if job_status is JobStatus.MANUAL_HOLD
                        else "queue_enqueued"
                    ),
                    current=current,
                )
                return QueueHandoffResult(
                    execution_id=execution.id,
                    job_id=job.id,
                    decision=decision,
                    job_status=job.status,
                    scheduled_for=job.scheduled_for,
                )

    async def _mark_execution_queued(
        self,
        session: AsyncSession,
        execution: RouteExecution,
        job: PublishJob,
        *,
        reason_code: str,
        current: datetime,
    ) -> None:
        if execution.status is RouteExecutionStatus.READY_FOR_QUEUE:
            await RouteExecutionService(session, self.account_id).transition(
                execution.id,
                RouteExecutionStatus.QUEUED,
                reason_code=reason_code,
                publish_job_id=job.id,
            )
        elif execution.status is not RouteExecutionStatus.QUEUED:
            raise QueueError("route_execution_queue_state_invalid")

        await SourceCheckpointCoordinator(session, self.account_id).try_advance(
            source_id=execution.source_id,
            cursor_message_id=execution.cursor_message_id,
            event_at=current,
        )

    def _initial_state(
        self,
        *,
        mode: PublishingMode,
        schedule: ScheduleProfile | None,
        current: datetime,
    ) -> tuple[JobStatus, datetime, QueueHandoffDecision]:
        if mode is PublishingMode.MANUAL:
            return JobStatus.MANUAL_HOLD, current, QueueHandoffDecision.MANUAL_HOLD
        if mode is PublishingMode.DIRECT:
            return JobStatus.QUEUED, current, QueueHandoffDecision.ENQUEUED
        return (
            JobStatus.QUEUED,
            next_run_at(schedule, current),
            QueueHandoffDecision.ENQUEUED,
        )

    async def claim_due(
        self,
        *,
        worker_id: str,
        limit: int = 10,
        now: datetime | None = None,
        lease_seconds: int | None = None,
    ) -> list[PublishJob]:
        worker = _worker_id(worker_id)
        if limit <= 0:
            raise QueueError("limit must be positive")
        lease = lease_seconds or self.default_lease_seconds
        if lease <= 0:
            raise QueueError("lease_seconds must be positive")
        current = _utc(now)

        async with self.session_factory() as session:
            async with session.begin():
                statement = (
                    select(PublishJob)
                    .where(
                        PublishJob.account_id == self.account_id,
                        PublishJob.route_execution_id.is_not(None),
                        PublishJob.route_payload_id.is_not(None),
                        PublishJob.scheduled_for <= current,
                        PublishJob.attempt_count < PublishJob.max_attempts,
                        or_(
                            PublishJob.status == JobStatus.QUEUED,
                            (
                                (PublishJob.status == JobStatus.RETRY_WAIT)
                                & (
                                    PublishJob.next_attempt_at.is_(None)
                                    | (PublishJob.next_attempt_at <= current)
                                )
                            ),
                        ),
                    )
                    .order_by(
                        PublishJob.priority,
                        PublishJob.scheduled_for,
                        PublishJob.created_at,
                        PublishJob.id,
                    )
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
                jobs = list((await session.scalars(statement)).all())
                for job in jobs:
                    job.status = JobStatus.PROCESSING
                    job.locked_at = current
                    job.lease_expires_at = current + timedelta(seconds=lease)
                    job.locked_by = worker
                    job.next_attempt_at = None
                    job.attempt_count += 1
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

    async def renew_lease(
        self,
        job_id: uuid.UUID,
        *,
        worker_id: str,
        now: datetime | None = None,
        lease_seconds: int | None = None,
    ) -> datetime:
        current = _utc(now)
        lease = lease_seconds or self.default_lease_seconds
        if lease <= 0:
            raise QueueError("lease_seconds must be positive")
        worker = _worker_id(worker_id)

        async with self.session_factory() as session:
            async with session.begin():
                job = await self._owned_claim(session, job_id, worker)
                job.lease_expires_at = current + timedelta(seconds=lease)
                await session.flush()
                return job.lease_expires_at

    async def mark_retry(
        self,
        job_id: uuid.UUID,
        *,
        worker_id: str,
        error_code: str,
        error_message: str,
        now: datetime | None = None,
        retry_after_seconds: int | None = None,
    ) -> JobStatus:
        current = _utc(now)
        worker = _worker_id(worker_id)
        async with self.session_factory() as session:
            async with session.begin():
                job = await self._owned_claim(session, job_id, worker)
                if job.attempt_count >= job.max_attempts:
                    await self._fail_locked_job(
                        session,
                        job,
                        error_code="max_attempts_exhausted",
                        error_message=error_message,
                        current=current,
                    )
                    return JobStatus.FAILED

                delay = retry_delay_seconds(
                    attempt_count=job.attempt_count,
                    base_seconds=self.retry_base_seconds,
                    cap_seconds=self.retry_cap_seconds,
                    retry_after_seconds=retry_after_seconds,
                )
                job.status = JobStatus.RETRY_WAIT
                job.next_attempt_at = current + timedelta(seconds=delay)
                job.last_error_code = _error_code(error_code)
                job.last_error_message = error_message[:4000]
                self._clear_lease(job)
                await self._finish_attempt(
                    session,
                    job,
                    AttemptStatus.RETRYING,
                    error_code=error_code,
                    error_message=error_message,
                    finished_at=current,
                )
                return job.status

    async def mark_failed(
        self,
        job_id: uuid.UUID,
        *,
        worker_id: str,
        error_code: str,
        error_message: str,
        now: datetime | None = None,
    ) -> None:
        current = _utc(now)
        worker = _worker_id(worker_id)
        async with self.session_factory() as session:
            async with session.begin():
                job = await self._owned_claim(session, job_id, worker)
                await self._fail_locked_job(
                    session,
                    job,
                    error_code=error_code,
                    error_message=error_message,
                    current=current,
                )

    async def recover_expired_leases(
        self,
        *,
        now: datetime | None = None,
        limit: int = 100,
    ) -> int:
        if limit <= 0:
            raise QueueError("limit must be positive")
        current = _utc(now)
        async with self.session_factory() as session:
            async with session.begin():
                jobs = list(
                    (
                        await session.scalars(
                            select(PublishJob)
                            .where(
                                PublishJob.account_id == self.account_id,
                                PublishJob.route_execution_id.is_not(None),
                                PublishJob.route_payload_id.is_not(None),
                                PublishJob.status.in_(
                                    (JobStatus.PROCESSING, JobStatus.PUBLISHING)
                                ),
                                PublishJob.lease_expires_at.is_not(None),
                                PublishJob.lease_expires_at <= current,
                            )
                            .order_by(PublishJob.lease_expires_at, PublishJob.id)
                            .limit(limit)
                            .with_for_update(skip_locked=True)
                        )
                    ).all()
                )
                for job in jobs:
                    if job.attempt_count >= job.max_attempts:
                        await self._fail_locked_job(
                            session,
                            job,
                            error_code="lease_expired_max_attempts",
                            error_message="worker lease expired after final allowed attempt",
                            current=current,
                        )
                        continue

                    delay = retry_delay_seconds(
                        attempt_count=max(job.attempt_count, 1),
                        base_seconds=self.retry_base_seconds,
                        cap_seconds=self.retry_cap_seconds,
                    )
                    job.status = JobStatus.RETRY_WAIT
                    job.next_attempt_at = current + timedelta(seconds=delay)
                    job.last_error_code = "lease_expired"
                    job.last_error_message = "worker lease expired before completion"
                    self._clear_lease(job)
                    await self._finish_attempt_if_present(
                        session,
                        job,
                        AttemptStatus.RETRYING,
                        error_code="lease_expired",
                        error_message="worker lease expired before completion",
                        finished_at=current,
                    )
                await session.flush()
                return len(jobs)

    async def release_manual(
        self,
        job_id: uuid.UUID,
        *,
        now: datetime | None = None,
    ) -> None:
        current = _utc(now)
        async with self.session_factory() as session:
            async with session.begin():
                job = await session.scalar(
                    select(PublishJob)
                    .where(
                        PublishJob.id == job_id,
                        PublishJob.account_id == self.account_id,
                    )
                    .with_for_update()
                )
                if job is None:
                    raise QueueError("publish_job_unavailable")
                if job.status is not JobStatus.MANUAL_HOLD:
                    raise QueueError("publish_job_not_manual_hold")
                job.status = JobStatus.QUEUED
                job.scheduled_for = current
                job.next_attempt_at = None
                await session.flush()

    async def _owned_claim(
        self,
        session: AsyncSession,
        job_id: uuid.UUID,
        worker_id: str,
    ) -> PublishJob:
        job = await session.scalar(
            select(PublishJob)
            .where(
                PublishJob.id == job_id,
                PublishJob.account_id == self.account_id,
            )
            .with_for_update()
        )
        if job is None:
            raise QueueError("publish_job_unavailable")
        if job.route_execution_id is None or job.route_payload_id is None:
            raise QueueError("publish_job_not_v3")
        if job.status not in {JobStatus.PROCESSING, JobStatus.PUBLISHING}:
            raise QueueError("publish_job_not_claimed")
        if job.locked_by != worker_id:
            raise QueueError("publish_job_owned_by_another_worker")
        return job

    async def _fail_locked_job(
        self,
        session: AsyncSession,
        job: PublishJob,
        *,
        error_code: str,
        error_message: str,
        current: datetime,
    ) -> None:
        job.status = JobStatus.FAILED
        job.last_error_code = _error_code(error_code)
        job.last_error_message = error_message[:4000]
        job.next_attempt_at = None
        self._clear_lease(job)
        await self._finish_attempt_if_present(
            session,
            job,
            AttemptStatus.FAILED,
            error_code=error_code,
            error_message=error_message,
            finished_at=current,
        )
        if job.route_execution_id is not None:
            service = RouteExecutionService(session, self.account_id)
            execution = await service.executions.get(job.route_execution_id)
            if execution is not None and execution.status is RouteExecutionStatus.QUEUED:
                await service.transition(
                    execution.id,
                    RouteExecutionStatus.FAILED,
                    reason_code=_error_code(error_code),
                )

    @staticmethod
    def _clear_lease(job: PublishJob) -> None:
        job.locked_at = None
        job.lease_expires_at = None
        job.locked_by = None

    @staticmethod
    async def _finish_attempt(
        session: AsyncSession,
        job: PublishJob,
        status: AttemptStatus,
        *,
        error_code: str,
        error_message: str,
        finished_at: datetime,
    ) -> None:
        attempt = await session.scalar(
            select(PublicationAttempt).where(
                PublicationAttempt.publish_job_id == job.id,
                PublicationAttempt.attempt_number == job.attempt_count,
            )
        )
        if attempt is None:
            raise QueueError("publication_attempt_missing")
        attempt.status = status
        attempt.error_code = _error_code(error_code)
        attempt.error_message = error_message[:4000]
        attempt.finished_at = finished_at

    @classmethod
    async def _finish_attempt_if_present(
        cls,
        session: AsyncSession,
        job: PublishJob,
        status: AttemptStatus,
        *,
        error_code: str,
        error_message: str,
        finished_at: datetime,
    ) -> None:
        attempt = await session.scalar(
            select(PublicationAttempt).where(
                PublicationAttempt.publish_job_id == job.id,
                PublicationAttempt.attempt_number == job.attempt_count,
            )
        )
        if attempt is None:
            return
        attempt.status = status
        attempt.error_code = _error_code(error_code)
        attempt.error_message = error_message[:4000]
        attempt.finished_at = finished_at


def _max_attempts(
    route_settings: object,
    destination_settings: object,
    default: int,
) -> int:
    for settings in (route_settings, destination_settings):
        if not isinstance(settings, dict):
            continue
        value = settings.get("max_attempts")
        if value is None:
            continue
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 100:
            raise QueueError("max_attempts_invalid")
        return value
    return default


def _worker_id(value: str) -> str:
    worker = value.strip()
    if not worker:
        raise QueueError("worker_id_required")
    if len(worker) > 255:
        raise QueueError("worker_id_too_long")
    return worker


def _error_code(value: str) -> str:
    code = value.strip()
    if not code:
        return "unknown_error"
    return code[:120]


def _utc(value: datetime | None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        raise QueueError("queue_time_must_be_timezone_aware")
    return current.astimezone(UTC)
