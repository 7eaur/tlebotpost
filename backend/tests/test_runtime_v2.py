from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest

from app.content import PipelineDecision, PipelineResult
from app.runtime_v2 import RuntimeConfigurationError, RuntimeV2Settings
from app.telegram.v2_listener import IngestionEvent


def test_runtime_settings_require_v2_account_id(monkeypatch):
    for name in ("API_ID", "API_HASH", "BOT_TOKEN", "V2_ACCOUNT_ID"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RuntimeConfigurationError, match="API_ID"):
        RuntimeV2Settings.from_env(env_file=None)


def test_runtime_settings_validate_values(monkeypatch):
    monkeypatch.setenv("API_ID", "123")
    monkeypatch.setenv("API_HASH", "hash")
    monkeypatch.setenv("BOT_TOKEN", "token")
    monkeypatch.setenv("V2_ACCOUNT_ID", str(uuid.uuid4()))
    monkeypatch.setenv("V2_QUEUE_BATCH_SIZE", "0")
    with pytest.raises(RuntimeConfigurationError, match="QUEUE_BATCH_SIZE"):
        RuntimeV2Settings.from_env(env_file=None)


def test_runtime_settings_parse_valid_environment(monkeypatch):
    account_id = uuid.uuid4()
    monkeypatch.setenv("API_ID", "123")
    monkeypatch.setenv("API_HASH", "hash")
    monkeypatch.setenv("BOT_TOKEN", "token")
    monkeypatch.setenv("V2_ACCOUNT_ID", str(account_id))
    settings = RuntimeV2Settings.from_env(env_file=None)
    assert settings.account_id == account_id
    assert settings.queue_batch_size == 10


def test_runtime_ingestion_callback_processes_and_enqueues_only_accepted():
    async def scenario():
        from app.runtime_v2 import RuntimeV2

        event = IngestionEvent(
            account_id=uuid.uuid4(),
            source_id=uuid.uuid4(),
            route_id=uuid.uuid4(),
            destination_id=uuid.uuid4(),
            chat_id=-1001,
            message_id=12,
            grouped_id=None,
            message=SimpleNamespace(text="hello"),
            received_at=SimpleNamespace(),
        )
        processed = []
        enqueued = []

        class Pipeline:
            async def process(self, received):
                processed.append(received)
                return PipelineResult(PipelineDecision.ACCEPTED, "accepted", received, uuid.uuid4())

        class Queue:
            async def enqueue_result(self, result):
                enqueued.append(result)

        runtime = object.__new__(RuntimeV2)
        runtime.pipeline = Pipeline()
        runtime.queue = Queue()
        runtime._logger = SimpleNamespace(info=lambda *args, **kwargs: None)

        await runtime.handle_ingestion(event)
        assert processed == [event]
        assert len(enqueued) == 1

    asyncio.run(scenario())


def test_runtime_ingestion_does_not_enqueue_filtered_result():
    async def scenario():
        from app.runtime_v2 import RuntimeV2

        event = IngestionEvent(
            account_id=uuid.uuid4(),
            source_id=uuid.uuid4(),
            route_id=uuid.uuid4(),
            destination_id=uuid.uuid4(),
            chat_id=-1001,
            message_id=12,
            grouped_id=None,
            message=SimpleNamespace(text="hello"),
            received_at=SimpleNamespace(),
        )
        calls = []

        class Pipeline:
            async def process(self, received):
                return PipelineResult(PipelineDecision.FILTERED, "excluded", received)

        class Queue:
            async def enqueue_result(self, result):
                calls.append(result)

        runtime = object.__new__(RuntimeV2)
        runtime.pipeline = Pipeline()
        runtime.queue = Queue()
        runtime._logger = SimpleNamespace(info=lambda *args, **kwargs: None)
        await runtime.handle_ingestion(event)
        assert calls == []

    asyncio.run(scenario())
