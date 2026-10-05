from __future__ import annotations

import asyncio

from app.database import Database
from app.repositories.sources import SourceRepository


def test_source_repository_is_live_only_and_monotonic(tmp_path):
    async def scenario():
        database = Database(tmp_path / "relay.sqlite3")
        await database.initialize()
        repository = SourceRepository(database)

        source = await repository.upsert(
            chat_id=-1001,
            input_ref="@source",
            title="Source",
            username="source",
            baseline_message_id=50,
        )
        assert source.baseline_message_id == 50
        assert source.enabled is True

        assert await repository.advance_baseline(-1001, 42) is True
        assert (await repository.get_by_chat_id(-1001)).baseline_message_id == 50
        await repository.advance_baseline(-1001, 51)
        assert (await repository.get_by_chat_id(-1001)).baseline_message_id == 51

        assert await repository.set_enabled(-1001, False) is True
        assert await repository.list(enabled_only=True) == []
        assert await repository.delete(-1001) is True
        assert await repository.get_by_chat_id(-1001) is None

    asyncio.run(scenario())
