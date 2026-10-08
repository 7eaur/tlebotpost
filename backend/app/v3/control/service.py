"""PostgreSQL-backed V3 control plane used by Telegram and future UIs."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    Destination,
    DestinationStatus,
    JobStatus,
    Project,
    ProjectStatus,
    PublishingMode,
    PublishJob,
    RouteStatus,
    Source,
    SourceCheckpoint,
    SourceRoute,
    SourceStatus,
    TelegramAccount,
)
from app.telegram.chat_resolver import ResolvedChat
from app.v3.publish_queue import PublishQueueV3

from .contracts import (
    ControlStatus,
    DestinationView,
    ProjectView,
    RouteView,
    SourceView,
)
from .errors import ControlServiceError


class ChatResolver(Protocol):
    async def __call__(self, input_ref: str) -> ResolvedChat: ...


ReloadCallback = Callable[[], Awaitable[None]]


class ControlServiceV3:
    """Own account-scoped configuration mutations and runtime reload requests."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        account_id: uuid.UUID,
        telegram_account_id: uuid.UUID,
        resolve_chat: ChatResolver,
        reload_callback: ReloadCallback | None,
        queue: PublishQueueV3,
        telegram_connected: Callable[[], bool] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.account_id = account_id
        self.telegram_account_id = telegram_account_id
        self.resolve_chat = resolve_chat
        self.reload_callback = reload_callback
        self.queue = queue
        self.telegram_connected = telegram_connected or (lambda: False)

    async def status(self) -> ControlStatus:
        async with self.session_factory() as session:
            telegram = await session.scalar(
                select(TelegramAccount).where(
                    TelegramAccount.id == self.telegram_account_id,
                    TelegramAccount.account_id == self.account_id,
                )
            )
            if telegram is None:
                raise ControlServiceError("telegram_account_unavailable")

            projects_total = await self._count(session, Project)
            projects_active = await self._count(
                session,
                Project,
                Project.status == ProjectStatus.ACTIVE,
            )
            sources_total = await self._count(session, Source)
            sources_active = await self._count(
                session,
                Source,
                Source.status == SourceStatus.ACTIVE,
            )
            destinations_total = await self._count(session, Destination)
            destinations_active = await self._count(
                session,
                Destination,
                Destination.status == DestinationStatus.ACTIVE,
            )
            routes_total = await self._count(session, SourceRoute)
            routes_active = await self._count(
                session,
                SourceRoute,
                SourceRoute.status == RouteStatus.ACTIVE,
            )
            pending_statuses = (
                JobStatus.QUEUED,
                JobStatus.MANUAL_HOLD,
                JobStatus.PROCESSING,
                JobStatus.PUBLISHING,
                JobStatus.RETRY_WAIT,
            )
            queue_pending = await self._count(
                session,
                PublishJob,
                PublishJob.route_execution_id.is_not(None),
                PublishJob.status.in_(pending_statuses),
            )
            queue_failed = await self._count(
                session,
                PublishJob,
                PublishJob.route_execution_id.is_not(None),
                PublishJob.status == JobStatus.FAILED,
            )
            latest_failed = await session.scalar(
                select(PublishJob)
                .where(
                    PublishJob.account_id == self.account_id,
                    PublishJob.route_execution_id.is_not(None),
                    PublishJob.status == JobStatus.FAILED,
                )
                .order_by(PublishJob.updated_at.desc(), PublishJob.id.desc())
                .limit(1)
            )
            return ControlStatus(
                telegram_status=telegram.status.value,
                telegram_connected=bool(self.telegram_connected()),
                telegram_last_error=telegram.last_error_code,
                projects_total=projects_total,
                projects_active=projects_active,
                sources_total=sources_total,
                sources_active=sources_active,
                destinations_total=destinations_total,
                destinations_active=destinations_active,
                routes_total=routes_total,
                routes_active=routes_active,
                queue_pending=queue_pending,
                queue_failed=queue_failed,
                last_publish_error=(
                    latest_failed.last_error_code if latest_failed is not None else None
                ),
            )

    async def list_projects(self) -> tuple[ProjectView, ...]:
        async with self.session_factory() as session:
            rows = (
                await session.scalars(
                    select(Project)
                    .where(Project.account_id == self.account_id)
                    .order_by(Project.name, Project.id)
                )
            ).all()
            return tuple(
                ProjectView(
                    id=row.id,
                    name=row.name,
                    slug=row.slug,
                    status=row.status.value,
                )
                for row in rows
            )

    async def list_sources(self) -> tuple[SourceView, ...]:
        async with self.session_factory() as session:
            rows = (
                await session.execute(
                    select(Source, SourceCheckpoint)
                    .outerjoin(SourceCheckpoint, SourceCheckpoint.source_id == Source.id)
                    .where(
                        Source.account_id == self.account_id,
                        Source.telegram_account_id == self.telegram_account_id,
                    )
                    .order_by(Source.title, Source.id)
                )
            ).all()
            return tuple(
                SourceView(
                    id=source.id,
                    title=source.title,
                    chat_id=source.telegram_chat_id,
                    username=source.telegram_username,
                    status=source.status.value,
                    last_seen_message_id=(
                        checkpoint.last_seen_message_id if checkpoint is not None else 0
                    ),
                )
                for source, checkpoint in rows
            )

    async def list_destinations(self) -> tuple[DestinationView, ...]:
        async with self.session_factory() as session:
            rows = (
                await session.scalars(
                    select(Destination)
                    .where(Destination.account_id == self.account_id)
                    .order_by(Destination.name, Destination.id)
                )
            ).all()
            return tuple(
                DestinationView(
                    id=row.id,
                    project_id=row.project_id,
                    name=row.name,
                    chat_id=row.telegram_chat_id,
                    username=row.telegram_username,
                    status=row.status.value,
                    publishing_mode=row.publishing_mode.value,
                )
                for row in rows
            )

    async def list_routes(self) -> tuple[RouteView, ...]:
        async with self.session_factory() as session:
            rows = (
                await session.scalars(
                    select(SourceRoute)
                    .where(SourceRoute.account_id == self.account_id)
                    .order_by(SourceRoute.created_at, SourceRoute.id)
                )
            ).all()
            return tuple(
                RouteView(
                    id=row.id,
                    source_id=row.source_id,
                    destination_id=row.destination_id,
                    status=row.status.value,
                    priority=int(row.priority),
                )
                for row in rows
            )

    async def add_source(self, input_ref: str) -> SourceView:
        resolved = await self._resolve_chat(input_ref)
        async with self.session_factory() as session:
            async with session.begin():
                telegram = await session.scalar(
                    select(TelegramAccount).where(
                        TelegramAccount.id == self.telegram_account_id,
                        TelegramAccount.account_id == self.account_id,
                    )
                )
                if telegram is None:
                    raise ControlServiceError("telegram_account_unavailable")

                source = await session.scalar(
                    select(Source)
                    .where(
                        Source.account_id == self.account_id,
                        Source.telegram_account_id == self.telegram_account_id,
                        Source.telegram_chat_id == resolved.chat_id,
                    )
                    .with_for_update()
                )
                if source is None:
                    source = Source(
                        account_id=self.account_id,
                        telegram_account_id=self.telegram_account_id,
                        telegram_chat_id=resolved.chat_id,
                        telegram_username=resolved.username,
                        title=resolved.title,
                        status=SourceStatus.ACTIVE,
                        metadata_json={"input_ref": resolved.input_ref},
                    )
                    session.add(source)
                    await session.flush()
                else:
                    source.telegram_username = resolved.username
                    source.title = resolved.title
                    source.status = SourceStatus.ACTIVE
                    source.metadata_json = {
                        **(source.metadata_json if isinstance(source.metadata_json, dict) else {}),
                        "input_ref": resolved.input_ref,
                    }

                checkpoint = await session.scalar(
                    select(SourceCheckpoint)
                    .where(SourceCheckpoint.source_id == source.id)
                    .with_for_update()
                )
                if checkpoint is None:
                    checkpoint = SourceCheckpoint(
                        source_id=source.id,
                        last_seen_message_id=resolved.latest_message_id,
                        last_committed_message_id=0,
                    )
                    session.add(checkpoint)
                elif resolved.latest_message_id > checkpoint.last_seen_message_id:
                    checkpoint.last_seen_message_id = resolved.latest_message_id
                await session.flush()
                view = SourceView(
                    id=source.id,
                    title=source.title,
                    chat_id=source.telegram_chat_id,
                    username=source.telegram_username,
                    status=source.status.value,
                    last_seen_message_id=checkpoint.last_seen_message_id,
                )
        await self._reload()
        return view

    async def set_source_active(self, source_id: str, *, active: bool) -> SourceView:
        identifier = _uuid(source_id, "source_id")
        async with self.session_factory() as session:
            async with session.begin():
                source = await session.scalar(
                    select(Source)
                    .where(
                        Source.id == identifier,
                        Source.account_id == self.account_id,
                        Source.telegram_account_id == self.telegram_account_id,
                    )
                    .with_for_update()
                )
                if source is None:
                    raise ControlServiceError("source_not_found")
                source.status = SourceStatus.ACTIVE if active else SourceStatus.PAUSED
                checkpoint = await session.get(SourceCheckpoint, source.id)
                await session.flush()
                view = SourceView(
                    id=source.id,
                    title=source.title,
                    chat_id=source.telegram_chat_id,
                    username=source.telegram_username,
                    status=source.status.value,
                    last_seen_message_id=(
                        checkpoint.last_seen_message_id if checkpoint is not None else 0
                    ),
                )
        await self._reload()
        return view

    async def add_destination(
        self,
        input_ref: str,
        *,
        project_selector: str | None = None,
    ) -> DestinationView:
        resolved = await self._resolve_chat(input_ref)
        async with self.session_factory() as session:
            async with session.begin():
                project = await self._resolve_project(session, project_selector)
                destination = await session.scalar(
                    select(Destination)
                    .where(
                        Destination.account_id == self.account_id,
                        Destination.telegram_chat_id == resolved.chat_id,
                    )
                    .with_for_update()
                )
                if destination is None:
                    destination = Destination(
                        account_id=self.account_id,
                        project_id=project.id,
                        name=resolved.title,
                        telegram_chat_id=resolved.chat_id,
                        telegram_username=resolved.username,
                        status=DestinationStatus.ACTIVE,
                        publishing_mode=PublishingMode.DIRECT,
                        settings={"input_ref": resolved.input_ref},
                    )
                    session.add(destination)
                else:
                    destination.project_id = project.id
                    destination.name = resolved.title
                    destination.telegram_username = resolved.username
                    destination.status = DestinationStatus.ACTIVE
                    destination.settings = {
                        **(
                            destination.settings
                            if isinstance(destination.settings, dict)
                            else {}
                        ),
                        "input_ref": resolved.input_ref,
                    }
                await session.flush()
                view = DestinationView(
                    id=destination.id,
                    project_id=destination.project_id,
                    name=destination.name,
                    chat_id=destination.telegram_chat_id,
                    username=destination.telegram_username,
                    status=destination.status.value,
                    publishing_mode=destination.publishing_mode.value,
                )
        await self._reload()
        return view

    async def set_destination_active(
        self,
        destination_id: str,
        *,
        active: bool,
    ) -> DestinationView:
        identifier = _uuid(destination_id, "destination_id")
        async with self.session_factory() as session:
            async with session.begin():
                destination = await session.scalar(
                    select(Destination)
                    .where(
                        Destination.id == identifier,
                        Destination.account_id == self.account_id,
                    )
                    .with_for_update()
                )
                if destination is None:
                    raise ControlServiceError("destination_not_found")
                destination.status = (
                    DestinationStatus.ACTIVE if active else DestinationStatus.PAUSED
                )
                await session.flush()
                view = DestinationView(
                    id=destination.id,
                    project_id=destination.project_id,
                    name=destination.name,
                    chat_id=destination.telegram_chat_id,
                    username=destination.telegram_username,
                    status=destination.status.value,
                    publishing_mode=destination.publishing_mode.value,
                )
        await self._reload()
        return view

    async def add_route(self, source_id: str, destination_id: str) -> RouteView:
        source_uuid = _uuid(source_id, "source_id")
        destination_uuid = _uuid(destination_id, "destination_id")
        async with self.session_factory() as session:
            async with session.begin():
                source = await session.scalar(
                    select(Source).where(
                        Source.id == source_uuid,
                        Source.account_id == self.account_id,
                        Source.telegram_account_id == self.telegram_account_id,
                    )
                )
                destination = await session.scalar(
                    select(Destination).where(
                        Destination.id == destination_uuid,
                        Destination.account_id == self.account_id,
                    )
                )
                if source is None:
                    raise ControlServiceError("source_not_found")
                if destination is None:
                    raise ControlServiceError("destination_not_found")

                route = await session.scalar(
                    select(SourceRoute)
                    .where(
                        SourceRoute.account_id == self.account_id,
                        SourceRoute.source_id == source.id,
                        SourceRoute.destination_id == destination.id,
                    )
                    .with_for_update()
                )
                if route is None:
                    route = SourceRoute(
                        account_id=self.account_id,
                        source_id=source.id,
                        destination_id=destination.id,
                        status=RouteStatus.ACTIVE,
                        publishing_mode=None,
                        priority=100,
                        settings={},
                    )
                    session.add(route)
                else:
                    route.status = RouteStatus.ACTIVE
                await session.flush()
                view = RouteView(
                    id=route.id,
                    source_id=route.source_id,
                    destination_id=route.destination_id,
                    status=route.status.value,
                    priority=int(route.priority),
                )
        await self._reload()
        return view

    async def set_route_active(self, route_id: str, *, active: bool) -> RouteView:
        identifier = _uuid(route_id, "route_id")
        async with self.session_factory() as session:
            async with session.begin():
                route = await session.scalar(
                    select(SourceRoute)
                    .where(
                        SourceRoute.id == identifier,
                        SourceRoute.account_id == self.account_id,
                    )
                    .with_for_update()
                )
                if route is None:
                    raise ControlServiceError("route_not_found")
                route.status = RouteStatus.ACTIVE if active else RouteStatus.PAUSED
                await session.flush()
                view = RouteView(
                    id=route.id,
                    source_id=route.source_id,
                    destination_id=route.destination_id,
                    status=route.status.value,
                    priority=int(route.priority),
                )
        await self._reload()
        return view

    async def set_project_active(
        self,
        *,
        project_selector: str | None,
        active: bool,
    ) -> ProjectView:
        async with self.session_factory() as session:
            async with session.begin():
                project = await self._resolve_project(
                    session,
                    project_selector,
                    for_update=True,
                )
                project.status = ProjectStatus.ACTIVE if active else ProjectStatus.PAUSED
                await session.flush()
                view = ProjectView(
                    id=project.id,
                    name=project.name,
                    slug=project.slug,
                    status=project.status.value,
                )
        await self._reload()
        return view

    async def release_manual_job(self, job_id: str) -> uuid.UUID:
        identifier = _uuid(job_id, "job_id")
        await self.queue.release_manual(identifier)
        return identifier

    async def reload_runtime(self) -> None:
        await self._reload(required=True)

    async def _resolve_chat(self, input_ref: str) -> ResolvedChat:
        value = input_ref.strip()
        if not value:
            raise ControlServiceError("telegram_reference_required")
        try:
            return await self.resolve_chat(value)
        except ControlServiceError:
            raise
        except Exception as exc:
            raise ControlServiceError("telegram_reference_unavailable") from exc

    async def _resolve_project(
        self,
        session: AsyncSession,
        selector: str | None,
        *,
        for_update: bool = False,
    ) -> Project:
        statement = select(Project).where(Project.account_id == self.account_id)
        if selector and selector.strip():
            value = selector.strip()
            try:
                identifier = uuid.UUID(value)
            except ValueError:
                statement = statement.where(
                    (Project.slug == value) | (Project.name == value)
                )
            else:
                statement = statement.where(Project.id == identifier)
            if for_update:
                statement = statement.with_for_update()
            project = await session.scalar(statement)
            if project is None:
                raise ControlServiceError("project_not_found")
            return project

        projects = list(
            (
                await session.scalars(
                    statement.order_by(Project.created_at, Project.id)
                )
            ).all()
        )
        if not projects:
            raise ControlServiceError("project_not_found")
        if len(projects) == 1:
            project = projects[0]
        else:
            active_projects = [
                item for item in projects if item.status is ProjectStatus.ACTIVE
            ]
            if len(active_projects) != 1:
                raise ControlServiceError("project_selector_required")
            project = active_projects[0]
        if for_update:
            project = await session.scalar(
                select(Project).where(Project.id == project.id).with_for_update()
            )
            if project is None:
                raise ControlServiceError("project_not_found")
        return project

    async def _reload(self, *, required: bool = False) -> None:
        if self.reload_callback is None:
            if required:
                raise ControlServiceError("runtime_reload_unavailable")
            return
        try:
            await self.reload_callback()
        except Exception as exc:
            raise ControlServiceError("configuration_saved_runtime_reload_failed") from exc

    async def _count(self, session: AsyncSession, model, *criteria) -> int:
        statement = select(func.count()).select_from(model).where(
            model.account_id == self.account_id,
            *criteria,
        )
        return int((await session.scalar(statement)) or 0)


def _uuid(value: str, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(value.strip())
    except (AttributeError, ValueError) as exc:
        raise ControlServiceError(f"{field}_invalid") from exc
