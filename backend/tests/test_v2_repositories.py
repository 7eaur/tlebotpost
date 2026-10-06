from __future__ import annotations

import asyncio
import uuid

from app.db.repositories import (
    DestinationRepository,
    ProjectRepository,
    SourceRepository,
    SourceRouteRepository,
)


class FakeSession:
    def __init__(self):
        self.added = []
        self.statements = []

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        return None

    async def scalar(self, statement):
        self.statements.append(statement)
        return None

    async def scalars(self, statement):
        self.statements.append(statement)
        return self

    def all(self):
        return []


def test_repositories_create_account_scoped_entities():
    async def scenario():
        session = FakeSession()
        account_id = uuid.uuid4()
        project_id = uuid.uuid4()
        source_id = uuid.uuid4()
        destination_id = uuid.uuid4()
        telegram_account_id = uuid.uuid4()

        project = await ProjectRepository(session, account_id).create(
            name="Content", slug="content"
        )
        destination = await DestinationRepository(session, account_id).create(
            project_id=project_id,
            name="Target",
            telegram_chat_id=-1001,
        )
        source = await SourceRepository(session, account_id).create(
            telegram_account_id=telegram_account_id,
            telegram_chat_id=-1002,
            title="Source",
        )
        route = await SourceRouteRepository(session, account_id).create(
            source_id=source_id,
            destination_id=destination_id,
        )

        assert project.account_id == account_id
        assert destination.account_id == account_id
        assert source.account_id == account_id
        assert route.account_id == account_id
        assert session.added == [project, destination, source, route]

    asyncio.run(scenario())


def test_repositories_keep_account_filter_in_queries():
    async def scenario():
        session = FakeSession()
        account_id = uuid.uuid4()
        await DestinationRepository(session, account_id).get(uuid.uuid4())
        await SourceRepository(session, account_id).list()
        await SourceRouteRepository(session, account_id).list_for_source(uuid.uuid4())

        rendered = [str(statement) for statement in session.statements]
        assert len(rendered) == 3
        assert all("account_id" in statement for statement in rendered)

    asyncio.run(scenario())
