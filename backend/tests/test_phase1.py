from __future__ import annotations

import asyncio
import sqlite3

import pytest

from app.config import ConfigurationError, Settings
from app.database import Database


def test_settings_load_from_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("API_ID", "12345")
    monkeypatch.setenv("API_HASH", "hash")
    monkeypatch.setenv("BOT_TOKEN", "token")
    monkeypatch.setenv("OWNER_ID", "67890")
    monkeypatch.setenv("BRAND_LINK", "https://t.me/our_channel")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "relay.sqlite3"))
    monkeypatch.setenv("INCLUDE_KEYWORDS", "خبر, تقنية, ")

    settings = Settings.from_env(env_file=None)

    assert settings.api_id == 12345
    assert settings.owner_id == 67890
    assert settings.include_keywords == ("خبر", "تقنية")
    assert settings.database_path == tmp_path / "relay.sqlite3"


def test_settings_require_branding(monkeypatch):
    for name in ("API_ID", "API_HASH", "BOT_TOKEN", "OWNER_ID", "BRAND_FOOTER", "BRAND_LINK"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("API_ID", "1")
    monkeypatch.setenv("API_HASH", "hash")
    monkeypatch.setenv("BOT_TOKEN", "token")
    monkeypatch.setenv("OWNER_ID", "2")

    with pytest.raises(ConfigurationError, match="BRAND_FOOTER or BRAND_LINK"):
        Settings.from_env(env_file=None)


def test_database_schema_has_no_message_content(tmp_path):
    async def scenario():
        database = Database(tmp_path / "relay.sqlite3")
        await database.initialize()
        assert await database.count_content_columns() == 0
        await database.add_source(
            chat_id=-1001,
            input_ref="@source",
            title="Source",
            username="source",
            baseline_message_id=44,
        )
        await database.update_baseline(-1001, 45)
        await database.log_event(
            source_chat_id=-1001,
            source_message_id=45,
            event_type="published",
            status="success",
        )
        await database.prune_event_log(keep=1)

    asyncio.run(scenario())

    with sqlite3.connect(tmp_path / "relay.sqlite3") as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {"sources", "settings", "event_log"}.issubset(tables)
        assert "message_text" not in {
            row[1] for row in connection.execute("PRAGMA table_info(event_log)")
        }
