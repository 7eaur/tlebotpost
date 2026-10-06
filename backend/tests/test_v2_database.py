from __future__ import annotations

import pytest

from app.db import Database, DatabaseConfigurationError, DatabaseSettings, metadata


def test_v2_metadata_contains_core_tables():
    expected = {
        "accounts",
        "users",
        "projects",
        "categories",
        "destinations",
        "telegram_accounts",
        "sources",
        "source_routes",
        "content_items",
        "content_media",
        "content_fingerprints",
        "publish_jobs",
        "publication_attempts",
        "published_messages",
        "audit_logs",
    }
    assert expected.issubset(metadata.tables)


def test_database_settings_require_asyncpg_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(DatabaseConfigurationError, match="DATABASE_URL"):
        DatabaseSettings.from_env()


def test_database_settings_validate_pool_options(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/app")
    monkeypatch.setenv("DATABASE_POOL_SIZE", "3")
    monkeypatch.setenv("DATABASE_MAX_OVERFLOW", "2")
    monkeypatch.setenv("DATABASE_ECHO", "true")

    settings = DatabaseSettings.from_env()

    assert settings.pool_size == 3
    assert settings.max_overflow == 2
    assert settings.echo is True


def test_database_settings_reject_sync_driver(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/app")
    with pytest.raises(DatabaseConfigurationError, match="asyncpg"):
        DatabaseSettings.from_env()


def test_database_can_construct_engine_without_connecting():
    settings = DatabaseSettings("postgresql+asyncpg://user:pass@localhost/app")
    database = Database(settings)
    try:
        assert database.engine.url.drivername == "postgresql+asyncpg"
        assert database.session_factory is not None
    finally:
        # Disposal is async; construction itself must not connect to PostgreSQL.
        import asyncio

        asyncio.run(database.close())
