"""Persistence operations for bounded operational event metadata."""

from __future__ import annotations

from app.database import Database, utc_now


class EventLogRepository:
    """Store operational metadata without message text or media."""

    def __init__(self, database: Database) -> None:
        self.database = database

    async def record(
        self,
        *,
        source_chat_id: int | None,
        source_message_id: int | None,
        event_type: str,
        status: str,
        error_code: str | None = None,
    ) -> None:
        """Record one relay event."""
        async with self.database.connection() as connection:
            await connection.execute(
                """
                INSERT INTO event_log
                    (source_chat_id, source_message_id, event_type, status, error_code, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (source_chat_id, source_message_id, event_type, status, error_code, utc_now()),
            )
            await connection.commit()

    async def prune(self, keep: int = 1000) -> None:
        """Keep only the newest event metadata rows."""
        if keep < 0:
            raise ValueError("keep must be non-negative")
        async with self.database.connection() as connection:
            await connection.execute(
                """
                DELETE FROM event_log
                 WHERE id NOT IN (
                    SELECT id FROM event_log ORDER BY id DESC LIMIT ?
                 )
                """,
                (keep,),
            )
            await connection.commit()
