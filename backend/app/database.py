"""Minimal SQLite persistence without storing message content or media."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL UNIQUE,
    input_ref TEXT NOT NULL,
    title TEXT NOT NULL,
    username TEXT,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    baseline_message_id INTEGER NOT NULL DEFAULT 0 CHECK (baseline_message_id >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sources_enabled ON sources(enabled);

CREATE TABLE IF NOT EXISTS settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    target_ref TEXT,
    target_chat_id INTEGER,
    brand_footer TEXT NOT NULL DEFAULT '',
    brand_link TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
    include_keywords TEXT NOT NULL DEFAULT '[]',
    exclude_keywords TEXT NOT NULL DEFAULT '[]',
    allowed_media_types TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL
);

INSERT OR IGNORE INTO settings (id, updated_at) VALUES (1, CURRENT_TIMESTAMP);

CREATE TABLE IF NOT EXISTS event_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_chat_id INTEGER,
    source_message_id INTEGER,
    event_type TEXT NOT NULL,
    status TEXT NOT NULL,
    error_code TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_event_log_created_at ON event_log(created_at);
"""


class Database:
    """Async SQLite gateway for configuration and operational cursors."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    async def initialize(self) -> None:
        """Create the schema and its parent directory."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as connection:
            await connection.executescript(SCHEMA)
            await connection.commit()

    async def close(self) -> None:
        """Keep the gateway API ready for a future pooled connection."""

    async def count_content_columns(self) -> int:
        """Return columns that could store message content; expected result is zero."""
        content_names = {"message_text", "caption", "media", "file_path", "content"}
        async with aiosqlite.connect(self.path) as connection:
            cursor = await connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
            tables = [row[0] for row in await cursor.fetchall()]
            count = 0
            for table in tables:
                columns_cursor = await connection.execute(f'PRAGMA table_info("{table}")')
                columns = {row[1] for row in await columns_cursor.fetchall()}
                count += len(columns & content_names)
            return count

    async def add_source(
        self,
        *,
        chat_id: int,
        input_ref: str,
        title: str,
        username: str | None,
        baseline_message_id: int,
    ) -> None:
        """Insert or refresh a source cursor without importing old messages."""
        now = _now()
        async with aiosqlite.connect(self.path) as connection:
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

    async def update_baseline(self, chat_id: int, message_id: int) -> None:
        """Advance a source cursor monotonically."""
        async with aiosqlite.connect(self.path) as connection:
            await connection.execute(
                """
                UPDATE sources
                   SET baseline_message_id = MAX(baseline_message_id, ?), updated_at = ?
                 WHERE chat_id = ?
                """,
                (message_id, _now(), chat_id),
            )
            await connection.commit()

    async def log_event(
        self,
        *,
        source_chat_id: int | None,
        source_message_id: int | None,
        event_type: str,
        status: str,
        error_code: str | None = None,
    ) -> None:
        """Write metadata only; message text and media are intentionally excluded."""
        async with aiosqlite.connect(self.path) as connection:
            await connection.execute(
                """
                INSERT INTO event_log
                    (source_chat_id, source_message_id, event_type, status, error_code, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (source_chat_id, source_message_id, event_type, status, error_code, _now()),
            )
            await connection.commit()

    async def prune_event_log(self, keep: int = 1000) -> None:
        """Keep only recent operational metadata so the database stays small."""
        if keep < 0:
            raise ValueError("keep must be non-negative")
        async with aiosqlite.connect(self.path) as connection:
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


def _now() -> str:
    return datetime.now(UTC).isoformat()
