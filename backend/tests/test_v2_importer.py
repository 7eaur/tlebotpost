from __future__ import annotations

import asyncio
import json

import aiosqlite
import pytest

from app.database import SCHEMA
from app.db.importer import LegacyImportError, import_legacy_snapshot, load_legacy_snapshot


def test_importer_loads_only_legacy_configuration(tmp_path):
    async def scenario():
        path = tmp_path / "legacy.sqlite3"
        async with aiosqlite.connect(path) as connection:
            await connection.executescript(SCHEMA)
            await connection.execute(
                """
                UPDATE settings
                   SET target_ref = ?, target_chat_id = ?, enabled = 1,
                       brand_footer = ?, brand_link = ?,
                       include_keywords = ?, exclude_keywords = ?, allowed_media_types = ?
                 WHERE id = 1
                """,
                (
                    "@target",
                    -100999,
                    "Footer",
                    "https://t.me/brand",
                    json.dumps(["one", "one", " two "]),
                    json.dumps(["spam"]),
                    json.dumps(["photo", "video"]),
                ),
            )
            await connection.execute(
                """
                INSERT INTO sources
                    (
                        chat_id, input_ref, title, username, enabled,
                        baseline_message_id, created_at, updated_at
                    )
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (-1001, "@source", "Source", "source", 1, 42),
            )
            await connection.commit()

        snapshot = await load_legacy_snapshot(path)
        assert snapshot.settings.target_chat_id == -100999
        assert snapshot.settings.include_keywords == ("one", "two")
        assert snapshot.sources[0].baseline_message_id == 42

    asyncio.run(scenario())


def test_importer_dry_run_never_writes_or_reads_content(tmp_path):
    async def scenario():
        path = tmp_path / "legacy.sqlite3"
        async with aiosqlite.connect(path) as connection:
            await connection.executescript(SCHEMA)
            await connection.commit()
        snapshot = await load_legacy_snapshot(path)
        report = await import_legacy_snapshot(object(), snapshot, dry_run=True)
        assert report.dry_run is True
        assert report.migrated_message_count == 0
        assert report.migrated_media_count == 0
        assert any("zero content" in warning for warning in report.warnings)

    asyncio.run(scenario())


def test_importer_rejects_missing_legacy_tables(tmp_path):
    async def scenario():
        path = tmp_path / "invalid.sqlite3"
        async with aiosqlite.connect(path) as connection:
            await connection.execute("CREATE TABLE settings (id INTEGER PRIMARY KEY)")
            await connection.commit()
        with pytest.raises(LegacyImportError, match="missing tables"):
            await load_legacy_snapshot(path)

    asyncio.run(scenario())


def test_importer_rejects_invalid_filter_json(tmp_path):
    async def scenario():
        path = tmp_path / "invalid.sqlite3"
        async with aiosqlite.connect(path) as connection:
            await connection.executescript(SCHEMA)
            await connection.execute(
                "UPDATE settings SET include_keywords = ? WHERE id = 1",
                ("not-json",),
            )
            await connection.commit()
        with pytest.raises(LegacyImportError, match="invalid JSON"):
            await load_legacy_snapshot(path)

    asyncio.run(scenario())
