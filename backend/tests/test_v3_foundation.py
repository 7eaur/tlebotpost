from __future__ import annotations

import uuid

import pytest

from app.db import DatabaseSettings
from app.v3 import RuntimeEnvironment, RuntimeState, RuntimeV3, RuntimeV3Settings
from app.v3.config import V3ConfigurationError


class FakeDatabase:
    def __init__(self, events: list[str], *, healthy: bool = True) -> None:
        self.events = events
        self.healthy = healthy

    async def ping(self) -> bool:
        self.events.append("db:ping")
        return self.healthy

    async def close(self) -> None:
        self.events.append("db:close")


class FakeComponent:
    def __init__(self, name: str, events: list[str], *, fail: bool = False) -> None:
        self.name = name
        self.events = events
        self.fail = fail

    async def start(self) -> None:
        self.events.append(f"{self.name}:start")
        if self.fail:
            raise RuntimeError(f"{self.name} failed")

    async def stop(self) -> None:
        self.events.append(f"{self.name}:stop")


def settings() -> RuntimeV3Settings:
    return RuntimeV3Settings(
        database=DatabaseSettings("postgresql+asyncpg://user:pass@localhost/app"),
        environment=RuntimeEnvironment.TEST,
    )


def test_v3_settings_allow_foundation_without_telegram(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/app")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    monkeypatch.delenv("API_ID", raising=False)
    monkeypatch.delenv("API_HASH", raising=False)
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.delenv("OWNER_ID", raising=False)

    loaded = RuntimeV3Settings.from_env(env_file=None, require_telegram=False)

    assert loaded.environment is RuntimeEnvironment.TEST
    assert loaded.log_level == "DEBUG"
    assert loaded.telegram is None


def test_v3_settings_require_telegram_as_one_group(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/app")
    monkeypatch.delenv("API_ID", raising=False)

    with pytest.raises(V3ConfigurationError, match="API_ID"):
        RuntimeV3Settings.from_env(env_file=None, require_telegram=True)


def test_v3_settings_parse_ingestion_scope(monkeypatch):
    account_id = uuid.uuid4()
    telegram_account_id = uuid.uuid4()
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/app")
    monkeypatch.setenv("API_ID", "123")
    monkeypatch.setenv("API_HASH", "hash")
    monkeypatch.setenv("V3_ACCOUNT_ID", str(account_id))
    monkeypatch.setenv("V3_TELEGRAM_ACCOUNT_ID", str(telegram_account_id))
    monkeypatch.setenv("V3_ALBUM_WINDOW_SECONDS", "0.4")
    monkeypatch.setenv("V3_RECONNECT_DELAYS", "1,2,3")

    loaded = RuntimeV3Settings.from_env(env_file=None, require_telegram=True)

    assert loaded.telegram is not None
    assert loaded.ingestion is not None
    assert loaded.ingestion.account_id == account_id
    assert loaded.ingestion.telegram_account_id == telegram_account_id
    assert loaded.ingestion.album_window_seconds == 0.4
    assert loaded.ingestion.reconnect_delays == (1.0, 2.0, 3.0)


def test_v3_publisher_settings_are_opt_in_and_enable_telegram_group(monkeypatch, tmp_path):
    account_id = uuid.uuid4()
    telegram_account_id = uuid.uuid4()
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/app")
    monkeypatch.setenv("API_ID", "123")
    monkeypatch.setenv("API_HASH", "hash")
    monkeypatch.setenv("BOT_TOKEN", "test-token")
    monkeypatch.setenv("V3_ACCOUNT_ID", str(account_id))
    monkeypatch.setenv("V3_TELEGRAM_ACCOUNT_ID", str(telegram_account_id))
    monkeypatch.setenv("V3_PUBLISHER_ENABLED", "true")
    monkeypatch.setenv("V3_PUBLISHER_WORKER_ID", "publisher-test")
    monkeypatch.setenv("V3_PUBLISHER_BATCH_SIZE", "4")
    monkeypatch.setenv("V3_PUBLISHER_LEASE_SECONDS", "45")
    monkeypatch.setenv("V3_MEDIA_STAGING_PATH", str(tmp_path / "stage"))

    loaded = RuntimeV3Settings.from_env(env_file=None, require_telegram=False)

    assert loaded.telegram is not None
    assert loaded.ingestion is not None
    assert loaded.publisher is not None
    assert loaded.publisher.worker_id == "publisher-test"
    assert loaded.publisher.queue_batch_size == 4
    assert loaded.publisher.lease_seconds == 45
    assert loaded.publisher.staging_path == tmp_path / "stage"


def test_v3_publisher_requires_bot_token(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/app")
    monkeypatch.setenv("API_ID", "123")
    monkeypatch.setenv("API_HASH", "hash")
    monkeypatch.setenv("V3_ACCOUNT_ID", str(uuid.uuid4()))
    monkeypatch.setenv("V3_TELEGRAM_ACCOUNT_ID", str(uuid.uuid4()))
    monkeypatch.setenv("V3_PUBLISHER_ENABLED", "true")
    monkeypatch.delenv("BOT_TOKEN", raising=False)

    with pytest.raises(V3ConfigurationError, match="BOT_TOKEN"):
        RuntimeV3Settings.from_env(env_file=None, require_telegram=False)


def test_v3_settings_reject_unknown_environment(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/app")
    monkeypatch.setenv("APP_ENV", "mystery")

    with pytest.raises(V3ConfigurationError, match="APP_ENV"):
        RuntimeV3Settings.from_env(env_file=None, require_telegram=False)


@pytest.mark.asyncio
async def test_runtime_starts_in_order_and_stops_in_reverse():
    events: list[str] = []
    first = FakeComponent("first", events)
    second = FakeComponent("second", events)
    runtime = RuntimeV3(
        database=FakeDatabase(events),
        settings=settings(),
        components=(first, second),
    )

    await runtime.start()
    assert runtime.state is RuntimeState.READY
    assert await runtime.readiness() == {
        "state": "ready",
        "database": True,
        "components_started": 2,
    }

    await runtime.stop()
    assert runtime.state is RuntimeState.STOPPED
    assert events == [
        "db:ping",
        "first:start",
        "second:start",
        "db:ping",
        "second:stop",
        "first:stop",
        "db:close",
    ]


@pytest.mark.asyncio
async def test_runtime_rolls_back_started_components_on_start_failure():
    events: list[str] = []
    first = FakeComponent("first", events)
    broken = FakeComponent("broken", events, fail=True)
    runtime = RuntimeV3(
        database=FakeDatabase(events),
        settings=settings(),
        components=(first, broken),
    )

    with pytest.raises(RuntimeError, match="broken failed"):
        await runtime.start()

    assert runtime.state is RuntimeState.FAILED
    assert events == ["db:ping", "first:start", "broken:start", "first:stop"]
    await runtime.stop()
    assert events[-1] == "db:close"


@pytest.mark.asyncio
async def test_runtime_fails_closed_when_database_is_unhealthy():
    events: list[str] = []
    runtime = RuntimeV3(
        database=FakeDatabase(events, healthy=False),
        settings=settings(),
    )

    with pytest.raises(RuntimeError, match="PostgreSQL readiness"):
        await runtime.start()

    assert runtime.state is RuntimeState.FAILED
