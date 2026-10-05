"""SQLite connection and schema management for the relay service."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
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
    """Manage the SQLite file and provide short-lived connections."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    async def initialize(self) -> None:
        """Create the schema and its parent directory."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with self.connection() as connection:
            await connection.executescript(SCHEMA)
            await connection.commit()

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[aiosqlite.Connection]:
        """Yield a configured connection and always close it afterwards."""
        connection = await aiosqlite.connect(self.path)
        connection.row_factory = aiosqlite.Row
        try:
            await connection.execute("PRAGMA foreign_keys = ON")
            yield connection
        finally:
            await connection.close()

    async def count_content_columns(self) -> int:
        """Return columns that could store message content; expected result is zero."""
        content_names = {"message_text", "caption", "media", "file_path", "content"}
        async with self.connection() as connection:
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


def utc_now() -> str:
    """Return an ISO-8601 UTC timestamp for persistence."""
    return datetime.now(UTC).isoformat()
