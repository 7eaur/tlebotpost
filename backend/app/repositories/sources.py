"""Persistence operations for monitored Telegram sources."""

from __future__ import annotations

from datetime import datetime

from app.database import Database, utc_now
from app.models import Source


class SourceRepository:
    """CRUD and monotonic cursor operations for source channels."""

    def __init__(self, database: Database) -> None:
        self.database = database

    async def upsert(
        self,
        *,
        chat_id: int,
        input_ref: str,
        title: str,
        username: str | None,
        baseline_message_id: int,
    ) -> Source:
        """Create a source or reset its live-only baseline explicitly."""
        if baseline_message_id < 0:
            raise ValueError("baseline_message_id must be non-negative")
        now = utc_now()
        async with self.database.connection() as connection:
            await connection.execute(
                """
                INSERT INTO sources
                    (
                        chat_id, input_ref, title, username,
                        baseline_message_id, created_at, updated_at
                    )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    input_ref = excluded.input_ref,
                    title = excluded.title,
                    username = excluded.username,
                    baseline_message_id = excluded.baseline_message_id,
                    updated_at = excluded.updated_at
                """,
                (chat_id, input_ref, title, username, baseline_message_id, now, now),
            )
            await connection.commit()
        source = await self.get_by_chat_id(chat_id)
        if source is None:
            raise RuntimeError("source was not available after upsert")
        return source

    async def get_by_chat_id(self, chat_id: int) -> Source | None:
        """Return one source by Telegram chat id."""
        async with self.database.connection() as connection:
            cursor = await connection.execute(
                "SELECT * FROM sources WHERE chat_id = ?",
                (chat_id,),
            )
            row = await cursor.fetchone()
        return _source_from_row(row) if row else None

    async def list(self, *, enabled_only: bool = False) -> list[Source]:
        """List sources, optionally limited to active sources."""
        query = "SELECT * FROM sources"
        params: tuple[int, ...] = ()
        if enabled_only:
            query += " WHERE enabled = ?"
            params = (1,)
        query += " ORDER BY id"
        async with self.database.connection() as connection:
            cursor = await connection.execute(query, params)
            rows = await cursor.fetchall()
        return [_source_from_row(row) for row in rows]

    async def set_enabled(self, chat_id: int, enabled: bool) -> bool:
        """Enable or disable a source and return whether it existed."""
        async with self.database.connection() as connection:
            cursor = await connection.execute(
                "UPDATE sources SET enabled = ?, updated_at = ? WHERE chat_id = ?",
                (int(enabled), utc_now(), chat_id),
            )
            await connection.commit()
            return cursor.rowcount == 1

    async def advance_baseline(self, chat_id: int, message_id: int) -> bool:
        """Move a source cursor forward only; never move it backwards."""
        if message_id < 0:
            raise ValueError("message_id must be non-negative")
        async with self.database.connection() as connection:
            cursor = await connection.execute(
                """
                UPDATE sources
                   SET baseline_message_id = MAX(baseline_message_id, ?), updated_at = ?
                 WHERE chat_id = ?
                """,
                (message_id, utc_now(), chat_id),
            )
            await connection.commit()
            return cursor.rowcount == 1

    async def delete(self, chat_id: int) -> bool:
        """Remove a source configuration without touching other sources."""
        async with self.database.connection() as connection:
            cursor = await connection.execute(
                "DELETE FROM sources WHERE chat_id = ?",
                (chat_id,),
            )
            await connection.commit()
            return cursor.rowcount == 1


def _source_from_row(row: object) -> Source:
    if row is None:
        raise ValueError("row is required")
    return Source(
        id=row["id"],
        chat_id=row["chat_id"],
        input_ref=row["input_ref"],
        title=row["title"],
        username=row["username"],
        enabled=bool(row["enabled"]),
        baseline_message_id=row["baseline_message_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )
