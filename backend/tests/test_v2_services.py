from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock

import pytest

from app.db import (
    DestinationService,
    DomainValidationError,
    EntityConflictError,
    EntityNotFoundError,
    ProjectService,
    SourceRouteService,
    validate_chat_id,
    validate_name,
    validate_slug,
)


class FakeSession:
    def __init__(self):
        self.added = []

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        return None

    async def scalar(self, _statement):
        return object()


def test_validation_rules_normalize_safe_values():
    assert validate_name("  Project  ") == "Project"
    assert validate_slug(" My-Project ") == "my-project"
    assert validate_chat_id(-100123) == -100123


def test_validation_rules_reject_invalid_values():
    with pytest.raises(DomainValidationError):
        validate_name("   ")
    with pytest.raises(DomainValidationError):
        validate_slug("Not A Slug")
    with pytest.raises(DomainValidationError):
        validate_chat_id(0)


def test_project_service_creates_normalized_project():
    async def scenario():
        session = FakeSession()
        project = await ProjectService(session, uuid.uuid4()).create(
            name="  Content  ", slug="Content-News"
        )
        assert project.name == "Content"
        assert project.slug == "content-news"
        assert session.added == [project]

    asyncio.run(scenario())


def test_destination_service_rejects_project_outside_account():
    async def scenario():
        service = DestinationService(FakeSession(), uuid.uuid4())
        service.projects.get = AsyncMock(return_value=None)
        with pytest.raises(EntityNotFoundError, match="project"):
            await service.create(
                project_id=uuid.uuid4(),
                name="Target",
                telegram_chat_id=-1001,
            )

    asyncio.run(scenario())


def test_route_service_rejects_duplicate_route():
    async def scenario():
        service = SourceRouteService(FakeSession(), uuid.uuid4())
        service.sources.get = AsyncMock(return_value=object())
        service.destinations.get = AsyncMock(return_value=object())
        service.routes.get_for_pair = AsyncMock(return_value=object())
        with pytest.raises(EntityConflictError, match="already exists"):
            await service.create(source_id=uuid.uuid4(), destination_id=uuid.uuid4())

    asyncio.run(scenario())
