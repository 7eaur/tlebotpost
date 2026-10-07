"""Durable source-to-destination execution contracts for V3."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
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
    SourceStatus,
)

CHECKPOINT_SAFE_STATUSES = frozenset(
    {
        RouteExecutionStatus.FILTERED,
        RouteExecutionStatus.DUPLICATE,
        RouteExecutionStatus.QUEUED,
        RouteExecutionStatus.PUBLISHED,
        RouteExecutionStatus.FAILED,
        RouteExecutionStatus.CANCELLED,
    }
)

FINAL_EXECUTION_STATUSES = frozenset(
    {
        RouteExecutionStatus.FILTERED,
        RouteExecutionStatus.DUPLICATE,
        RouteExecutionStatus.PUBLISHED,
        RouteExecutionStatus.FAILED,
        RouteExecutionStatus.CANCELLED,
    }
)

_ALLOWED_TRANSITIONS = {
    RouteExecutionStatus.RECEIVED: frozenset(
        {
            RouteExecutionStatus.PROCESSING,
            RouteExecutionStatus.FILTERED,
            RouteExecutionStatus.DUPLICATE,
            RouteExecutionStatus.QUEUED,
            RouteExecutionStatus.FAILED,
            RouteExecutionStatus.CANCELLED,
        }
    ),
    RouteExecutionStatus.PROCESSING: frozenset(
        {
            RouteExecutionStatus.READY_FOR_DEDUP,
            RouteExecutionStatus.FILTERED,
            RouteExecutionStatus.DUPLICATE,
            RouteExecutionStatus.QUEUED,
            RouteExecutionStatus.FAILED,
            RouteExecutionStatus.CANCELLED,
        }
    ),
    RouteExecutionStatus.READY_FOR_DEDUP: frozenset(
        {
            RouteExecutionStatus.READY_FOR_QUEUE,
            RouteExecutionStatus.DUPLICATE,
            RouteExecutionStatus.QUEUED,
            RouteExecutionStatus.FAILED,
            RouteExecutionStatus.CANCELLED,
        }
    ),
    RouteExecutionStatus.READY_FOR_QUEUE: frozenset(
        {
            RouteExecutionStatus.QUEUED,
            RouteExecutionStatus.FAILED,
            RouteExecutionStatus.CANCELLED,
        }
    ),
    RouteExecutionStatus.QUEUED: frozenset(
        {
            RouteExecutionStatus.PUBLISHED,
            RouteExecutionStatus.FAILED,
            RouteExecutionStatus.CANCELLED,
        }
    ),
}


class RouteExecutionError(RuntimeError):
    """Raised when a route-execution contract is violated."""


@dataclass(frozen=True, slots=True)
class EventRegistration:
    source_id: uuid.UUID
    event_key: str
    cursor_message_id: int
    executions: tuple[RouteExecution, ...]


def build_event_key(*, message_id: int | None, grouped_id: int | None) -> str:
    """Build stable Telegram identity for a single message or album."""
    if grouped_id is not None:
        if grouped_id <= 0:
            raise RouteExecutionError("grouped_id must be positive")
        return f"group:{grouped_id}"
    if message_id is None or message_id <= 0:
        raise RouteExecutionError("message_id must be positive when grouped_id is absent")
    return f"message:{message_id}"


class RouteExecutionRepository:
    """Account-scoped persistence for V3 route executions."""

    def __init__(self, session: AsyncSession, account_id: uuid.UUID) -> None:
        self.session = session
        self.account_id = account_id

    async def list_active_bindings_for_source(
        self, source_id: uuid.UUID
    ) -> Sequence[tuple[SourceRoute, Destination]]:
        statement = (
            select(SourceRoute, Destination)
            .join(Source, Source.id == SourceRoute.source_id)
            .join(Destination, Destination.id == SourceRoute.destination_id)
            .join(Project, Project.id == Destination.project_id)
            .where(
                SourceRoute.account_id == self.account_id,
                SourceRoute.source_id == source_id,
                SourceRoute.status == RouteStatus.ACTIVE,
                Source.account_id == self.account_id,
                Source.status == SourceStatus.ACTIVE,
                Destination.account_id == self.account_id,
                Destination.status == DestinationStatus.ACTIVE,
                Project.account_id == self.account_id,
                Project.status == ProjectStatus.ACTIVE,
            )
            .order_by(SourceRoute.priority, SourceRoute.created_at, SourceRoute.id)
        )
        return (await self.session.execute(statement)).all()

    async def get(self, execution_id: uuid.UUID) -> RouteExecution | None:
        return await self.session.scalar(
            select(RouteExecution).where(
                RouteExecution.id == execution_id,
                RouteExecution.account_id == self.account_id,
            )
        )

    async def get_for_route_event(
        self, route_id: uuid.UUID, event_key: str
    ) -> RouteExecution | None:
        return await self.session.scalar(
            select(RouteExecution).where(
                RouteExecution.account_id == self.account_id,
                RouteExecution.route_id == route_id,
                RouteExecution.event_key == event_key,
            )
        )

    async def create(
        self,
        *,
        source_id: uuid.UUID,
        route: SourceRoute,
        destination: Destination,
        event_key: str,
        cursor_message_id: int,
        telegram_message_id: int | None,
        telegram_grouped_id: int | None,
    ) -> RouteExecution:
        existing = await self.get_for_route_event(route.id, event_key)
        if existing is not None:
            return existing

        execution = RouteExecution(
            account_id=self.account_id,
            source_id=source_id,
            route_id=route.id,
            destination_id=destination.id,
            event_key=event_key,
            cursor_message_id=cursor_message_id,
            telegram_message_id=telegram_message_id,
            telegram_grouped_id=telegram_grouped_id,
            status=RouteExecutionStatus.RECEIVED,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(execution)
                await self.session.flush()
        except IntegrityError:
            existing = await self.get_for_route_event(route.id, event_key)
            if existing is None:
                raise
            return existing
        return execution

    async def list_for_cursor(
        self, source_id: uuid.UUID, cursor_message_id: int
    ) -> Sequence[RouteExecution]:
        statement = (
            select(RouteExecution)
            .where(
                RouteExecution.account_id == self.account_id,
                RouteExecution.source_id == source_id,
                RouteExecution.cursor_message_id == cursor_message_id,
            )
            .order_by(RouteExecution.created_at, RouteExecution.id)
        )
        return (await self.session.scalars(statement)).all()

    async def has_blocking_execution_through(
        self, source_id: uuid.UUID, cursor_message_id: int
    ) -> bool:
        statement = (
            select(RouteExecution.id)
            .where(
                RouteExecution.account_id == self.account_id,
                RouteExecution.source_id == source_id,
                RouteExecution.cursor_message_id <= cursor_message_id,
                RouteExecution.status.not_in(tuple(CHECKPOINT_SAFE_STATUSES)),
            )
            .limit(1)
        )
        return await self.session.scalar(statement) is not None


class RouteExecutionService:
    """Create and transition the durable route work generated by one source event."""

    def __init__(self, session: AsyncSession, account_id: uuid.UUID) -> None:
        self.session = session
        self.account_id = account_id
        self.executions = RouteExecutionRepository(session, account_id)

    async def register_event(
        self,
        *,
        source_id: uuid.UUID,
        cursor_message_id: int,
        telegram_message_id: int | None,
        telegram_grouped_id: int | None = None,
    ) -> EventRegistration:
        if cursor_message_id <= 0:
            raise RouteExecutionError("cursor_message_id must be positive")
        if telegram_message_id is not None and telegram_message_id <= 0:
            raise RouteExecutionError("telegram_message_id must be positive")
        if telegram_message_id is not None and cursor_message_id < telegram_message_id:
            raise RouteExecutionError("cursor_message_id cannot precede telegram_message_id")

        source = await self.session.scalar(
            select(Source).where(
                Source.id == source_id,
                Source.account_id == self.account_id,
            )
        )
        if source is None:
            raise RouteExecutionError("source is not available in this account")
        if source.status is not SourceStatus.ACTIVE:
            raise RouteExecutionError("source is not active")

        event_key = build_event_key(
            message_id=telegram_message_id,
            grouped_id=telegram_grouped_id,
        )
        bindings = await self.executions.list_active_bindings_for_source(source_id)
        created: list[RouteExecution] = []
        for route, destination in bindings:
            created.append(
                await self.executions.create(
                    source_id=source_id,
                    route=route,
                    destination=destination,
                    event_key=event_key,
                    cursor_message_id=cursor_message_id,
                    telegram_message_id=telegram_message_id,
                    telegram_grouped_id=telegram_grouped_id,
                )
            )
        return EventRegistration(
            source_id=source_id,
            event_key=event_key,
            cursor_message_id=cursor_message_id,
            executions=tuple(created),
        )

    async def transition(
        self,
        execution_id: uuid.UUID,
        status: RouteExecutionStatus,
        *,
        reason_code: str | None = None,
        content_item_id: uuid.UUID | None = None,
        publish_job_id: uuid.UUID | None = None,
        terminal_at: datetime | None = None,
    ) -> RouteExecution:
        execution = await self.executions.get(execution_id)
        if execution is None:
            raise RouteExecutionError("route execution is not available in this account")
        if status is execution.status:
            return execution

        allowed = _ALLOWED_TRANSITIONS.get(execution.status, frozenset())
        if status not in allowed:
            raise RouteExecutionError(
                f"invalid route execution transition: {execution.status.value} -> {status.value}"
            )

        execution.status = status
        execution.reason_code = _normalize_reason(reason_code)
        if content_item_id is not None:
            execution.content_item_id = content_item_id
        if publish_job_id is not None:
            execution.publish_job_id = publish_job_id
        if status in FINAL_EXECUTION_STATUSES:
            execution.terminal_at = terminal_at or datetime.now(UTC)
        await self.session.flush()
        return execution


class SourceCheckpointCoordinator:
    """Advance only through source events whose route outcomes are durably recorded."""

    def __init__(self, session: AsyncSession, account_id: uuid.UUID) -> None:
        self.session = session
        self.account_id = account_id
        self.executions = RouteExecutionRepository(session, account_id)

    async def try_advance(
        self,
        *,
        source_id: uuid.UUID,
        cursor_message_id: int,
        event_at: datetime | None = None,
    ) -> bool:
        if cursor_message_id <= 0:
            raise RouteExecutionError("cursor_message_id must be positive")

        source = await self.session.scalar(
            select(Source.id).where(
                Source.id == source_id,
                Source.account_id == self.account_id,
            )
        )
        if source is None:
            raise RouteExecutionError("source is not available in this account")

        executions = await self.executions.list_for_cursor(source_id, cursor_message_id)
        if not executions:
            return False
        if any(item.status not in CHECKPOINT_SAFE_STATUSES for item in executions):
            return False
        if await self.executions.has_blocking_execution_through(
            source_id, cursor_message_id
        ):
            return False

        checkpoint = await self.session.scalar(
            select(SourceCheckpoint)
            .where(SourceCheckpoint.source_id == source_id)
            .with_for_update()
        )
        if checkpoint is None:
            checkpoint = SourceCheckpoint(
                source_id=source_id,
                last_seen_message_id=0,
                last_committed_message_id=0,
                last_event_at=event_at,
            )
            self.session.add(checkpoint)
        if cursor_message_id > checkpoint.last_committed_message_id:
            checkpoint.last_committed_message_id = cursor_message_id
            checkpoint.last_event_at = event_at
        await self.session.flush()
        return True


def _normalize_reason(value: str | None) -> str | None:
    if value is None:
        return None
    reason = value.strip()
    if not reason:
        return None
    if len(reason) > 120:
        raise RouteExecutionError("reason_code cannot exceed 120 characters")
    return reason
