"""Account-scoped repositories for the v2 domain layer."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    Account,
    Destination,
    DestinationStatus,
    Project,
    ProjectStatus,
    Source,
    SourceRoute,
)


class AccountRepository:
    """Read account records without crossing tenant boundaries."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, account_id: uuid.UUID) -> Account | None:
        return await self.session.scalar(select(Account).where(Account.id == account_id))


class ProjectRepository:
    """Manage projects owned by one account."""

    def __init__(self, session: AsyncSession, account_id: uuid.UUID) -> None:
        self.session = session
        self.account_id = account_id

    async def list(self, *, status: ProjectStatus | None = None) -> Sequence[Project]:
        statement = select(Project).where(Project.account_id == self.account_id)
        if status is not None:
            statement = statement.where(Project.status == status)
        statement = statement.order_by(Project.created_at.desc())
        return (await self.session.scalars(statement)).all()

    async def get(self, project_id: uuid.UUID) -> Project | None:
        statement = select(Project).where(
            Project.account_id == self.account_id,
            Project.id == project_id,
        )
        return await self.session.scalar(statement)

    async def create(self, *, name: str, slug: str, description: str | None = None) -> Project:
        project = Project(
            account_id=self.account_id,
            name=name,
            slug=slug,
            description=description,
        )
        self.session.add(project)
        await self.session.flush()
        return project


class DestinationRepository:
    """Manage destinations within one account and optional project."""

    def __init__(self, session: AsyncSession, account_id: uuid.UUID) -> None:
        self.session = session
        self.account_id = account_id

    async def list(
        self,
        *,
        project_id: uuid.UUID | None = None,
        status: DestinationStatus | None = None,
    ) -> Sequence[Destination]:
        statement = select(Destination).where(Destination.account_id == self.account_id)
        if project_id is not None:
            statement = statement.where(Destination.project_id == project_id)
        if status is not None:
            statement = statement.where(Destination.status == status)
        statement = statement.order_by(Destination.created_at.desc())
        return (await self.session.scalars(statement)).all()

    async def get(self, destination_id: uuid.UUID) -> Destination | None:
        statement = select(Destination).where(
            Destination.account_id == self.account_id,
            Destination.id == destination_id,
        )
        return await self.session.scalar(statement)

    async def create(
        self,
        *,
        project_id: uuid.UUID,
        name: str,
        telegram_chat_id: int,
        telegram_username: str | None = None,
    ) -> Destination:
        destination = Destination(
            account_id=self.account_id,
            project_id=project_id,
            name=name,
            telegram_chat_id=telegram_chat_id,
            telegram_username=telegram_username,
        )
        self.session.add(destination)
        await self.session.flush()
        return destination


class SourceRepository:
    """Manage reusable Telegram sources within one account."""

    def __init__(self, session: AsyncSession, account_id: uuid.UUID) -> None:
        self.session = session
        self.account_id = account_id

    async def list(self, *, status: str | None = None) -> Sequence[Source]:
        statement = select(Source).where(Source.account_id == self.account_id)
        if status is not None:
            statement = statement.where(Source.status == status)
        statement = statement.order_by(Source.created_at.desc())
        return (await self.session.scalars(statement)).all()

    async def get(self, source_id: uuid.UUID) -> Source | None:
        statement = select(Source).where(
            Source.account_id == self.account_id,
            Source.id == source_id,
        )
        return await self.session.scalar(statement)

    async def create(
        self,
        *,
        telegram_account_id: uuid.UUID,
        telegram_chat_id: int,
        title: str,
        telegram_username: str | None = None,
    ) -> Source:
        source = Source(
            account_id=self.account_id,
            telegram_account_id=telegram_account_id,
            telegram_chat_id=telegram_chat_id,
            title=title,
            telegram_username=telegram_username,
        )
        self.session.add(source)
        await self.session.flush()
        return source


class SourceRouteRepository:
    """Manage source-to-destination routes scoped to one account."""

    def __init__(self, session: AsyncSession, account_id: uuid.UUID) -> None:
        self.session = session
        self.account_id = account_id

    async def list_for_destination(self, destination_id: uuid.UUID) -> Sequence[SourceRoute]:
        statement = select(SourceRoute).where(
            SourceRoute.account_id == self.account_id,
            SourceRoute.destination_id == destination_id,
        )
        return (await self.session.scalars(statement)).all()

    async def list_for_source(self, source_id: uuid.UUID) -> Sequence[SourceRoute]:
        statement = select(SourceRoute).where(
            SourceRoute.account_id == self.account_id,
            SourceRoute.source_id == source_id,
        )
        return (await self.session.scalars(statement)).all()

    async def get(self, route_id: uuid.UUID) -> SourceRoute | None:
        statement = select(SourceRoute).where(
            SourceRoute.account_id == self.account_id,
            SourceRoute.id == route_id,
        )
        return await self.session.scalar(statement)

    async def create(
        self,
        *,
        source_id: uuid.UUID,
        destination_id: uuid.UUID,
        priority: int = 100,
    ) -> SourceRoute:
        route = SourceRoute(
            account_id=self.account_id,
            source_id=source_id,
            destination_id=destination_id,
            priority=priority,
        )
        self.session.add(route)
        await self.session.flush()
        return route
