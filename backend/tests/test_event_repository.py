from __future__ import annotations

import asyncio
import sqlite3

from app.database import Database
from app.repositories.events import EventLogRepository


def test_event_repository_is_bounded_and_content_free(tmp_path):
    async def scenario():
        database = Database(tmp_path / "relay.sqlite3")
        await database.initialize()
        repository = EventLogRepository(database)
        for message_id in range(1, 4):
            await repository.record(
                source_chat_id=-1001,
                source_message_id=message_id,
                event_type="published",
                status="success",
            )
        await repository.prune(keep=2)

    asyncio.run(scenario())

    with sqlite3.connect(tmp_path / "relay.sqlite3") as connection:
        count = connection.execute("SELECT COUNT(*) FROM event_log").fetchone()[0]
        columns = {row[1] for row in connection.execute("PRAGMA table_info(event_log)")}
    assert count == 2
    assert "message_text" not in columns
    assert "media" not in columns
