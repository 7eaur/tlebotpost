"""Account-scoped application services for the v2 domain."""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    Destination,
    DestinationStatus,
    Project,
    ProjectStatus,
    RouteStatus,
    Source,
    SourceRoute,
    SourceStatus,
    TelegramAccount,
)
from .repositories import (
    DestinationRepository,
    ProjectRepository,
    SourceRepository,
    SourceRouteRepository,
)

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")


class DomainServiceError(RuntimeError):
    """Base error for expected v2 domain-service failures."""


class EntityNotFoundError(DomainServiceError):
    """Raised when an entity does not belong to the current account or does not exist."""


class EntityConflictError(DomainServiceError):
    """Raised when an operation conflicts with an existing domain relation."""


class DomainValidationError(DomainServiceError):
    """Raised when a service input violates domain rules."""


def validate_name(value: str, field: str = "name") -> str:
    value = value.strip()
    if not value:
        raise DomainValidationError(f"{field} cannot be empty")
    if len(value) > 160:
        raise DomainValidationError(f"{field} cannot exceed 160 characters")
    return value


def validate_slug(value: str) -> str:
    slug = value.strip().lower()
    if not _SLUG_RE.fullmatch(slug):
        raise DomainValidationError("slug must contain 2-63 lowercase letters, numbers, or hyphens")
    return slug


def validate_chat_id(value: int) -> int:
    if not isinstance(value, int) or value == 0:
        raise DomainValidationError("telegram_chat_id must be a non-zero integer")
    return value


class ProjectService:
    """Create and manage projects inside one account."""

    def __init__(self, session: AsyncSession, account_id: uuid.UUID) -> None:
        self.projects = ProjectRepository(session, account_id)

    async def list(self, *, status: ProjectStatus | None = None) -> Sequence[Project]:
        return await self.projects.list(status=status)

    async def get(self, project_id: uuid.UUID) -> Project:
        project = await self.projects.get(project_id)
        if project is None:
            raise EntityNotFoundError("project was not found in this account")
        return project

    async def create(
        self,
        *,
        name: str,
        slug: str,
        description: str | None = None,
    ) -> Project:
        return await self.projects.create(
            name=validate_name(name),
            slug=validate_slug(slug),
            description=description.strip() if description else None,
        )


class DestinationService:
    """Manage destinations and ensure they belong to an account project."""

    def __init__(self, session: AsyncSession, account_id: uuid.UUID) -> None:
        self.projects = ProjectRepository(session, account_id)
        self.destinations = DestinationRepository(session, account_id)

    async def list(
        self,
        *,
        project_id: uuid.UUID | None = None,
        status: DestinationStatus | None = None,
    ) -> Sequence[Destination]:
        if project_id is not None:
            await self._require_project(project_id)
        return await self.destinations.list(project_id=project_id, status=status)

    async def get(self, destination_id: uuid.UUID) -> Destination:
        destination = await self.destinations.get(destination_id)
        if destination is None:
            raise EntityNotFoundError("destination was not found in this account")
        return destination

    async def create(
        self,
        *,
        project_id: uuid.UUID,
        name: str,
        telegram_chat_id: int,
        telegram_username: str | None = None,
    ) -> Destination:
        await self._require_project(project_id)
        try:
            return await self.destinations.create(
                project_id=project_id,
                name=validate_name(name),
                telegram_chat_id=validate_chat_id(telegram_chat_id),
                telegram_username=telegram_username.strip() if telegram_username else None,
            )
        except IntegrityError as exc:
            raise EntityConflictError("destination already exists") from exc

    async def set_status(self, destination_id: uuid.UUID, status: DestinationStatus) -> Destination:
        destination = await self.get(destination_id)
        destination.status = status
        await self.destinations.session.flush()
        return destination

    async def _require_project(self, project_id: uuid.UUID) -> Project:
        project = await self.projects.get(project_id)
        if project is None:
            raise EntityNotFoundError("project was not found in this account")
        return project


class SourceService:
    """Manage reusable Telegram sources owned by the current account."""

    def __init__(self, session: AsyncSession, account_id: uuid.UUID) -> None:
        self.session = session
        self.account_id = account_id
        self.sources = SourceRepository(session, account_id)

    async def list(self, *, status: SourceStatus | None = None) -> Sequence[Source]:
        return await self.sources.list(status=status)

    async def get(self, source_id: uuid.UUID) -> Source:
        source = await self.sources.get(source_id)
        if source is None:
            raise EntityNotFoundError("source was not found in this account")
        return source

    async def create(
        self,
        *,
        telegram_account_id: uuid.UUID,
        telegram_chat_id: int,
        title: str,
        telegram_username: str | None = None,
    ) -> Source:
        account = await self.session.scalar(
            select(TelegramAccount).where(
                TelegramAccount.id == telegram_account_id,
                TelegramAccount.account_id == self.account_id,
            )
        )
        if account is None:
            raise EntityNotFoundError("telegram account was not found in this account")
        try:
            return await self.sources.create(
                telegram_account_id=telegram_account_id,
                telegram_chat_id=validate_chat_id(telegram_chat_id),
                title=validate_name(title, "title"),
                telegram_username=telegram_username.strip() if telegram_username else None,
            )
        except IntegrityError as exc:
            raise EntityConflictError("source already exists") from exc


class SourceRouteService:
    """Create and manage the source-to-destination relation safely."""

    def __init__(self, session: AsyncSession, account_id: uuid.UUID) -> None:
        self.session = session
        self.sources = SourceRepository(session, account_id)
        self.destinations = DestinationRepository(session, account_id)
        self.routes = SourceRouteRepository(session, account_id)

    async def get(self, route_id: uuid.UUID) -> SourceRoute:
        route = await self.routes.get(route_id)
        if route is None:
            raise EntityNotFoundError("source route was not found in this account")
        return route

    async def list_for_destination(self, destination_id: uuid.UUID) -> Sequence[SourceRoute]:
        await self._require_destination(destination_id)
        return await self.routes.list_for_destination(destination_id)

    async def list_for_source(self, source_id: uuid.UUID) -> Sequence[SourceRoute]:
        await self._require_source(source_id)
        return await self.routes.list_for_source(source_id)

    async def create(
        self,
        *,
        source_id: uuid.UUID,
        destination_id: uuid.UUID,
        priority: int = 100,
    ) -> SourceRoute:
        await self._require_source(source_id)
        await self._require_destination(destination_id)
        if priority < 0:
            raise DomainValidationError("priority cannot be negative")
        if await self.routes.get_for_pair(source_id, destination_id) is not None:
            raise EntityConflictError("source route already exists")
        try:
            return await self.routes.create(
                source_id=source_id,
                destination_id=destination_id,
                priority=priority,
            )
        except IntegrityError as exc:
            raise EntityConflictError("source route already exists") from exc

    async def set_status(self, route_id: uuid.UUID, status: RouteStatus) -> SourceRoute:
        route = await self.get(route_id)
        route.status = status
        await self.routes.session.flush()
        return route

    async def _require_source(self, source_id: uuid.UUID) -> Source:
        source = await self.sources.get(source_id)
        if source is None:
            raise EntityNotFoundError("source was not found in this account")
        return source

    async def _require_destination(self, destination_id: uuid.UUID) -> Destination:
        destination = await self.destinations.get(destination_id)
        if destination is None:
            raise EntityNotFoundError("destination was not found in this account")
        return destination
