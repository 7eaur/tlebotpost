from __future__ import annotations

import os

import pytest

from app.integration_smoke import run_postgres_smoke

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_runtime_v2_pipeline_and_queue_on_postgres():
    database_url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL or DATABASE_URL is required")
    try:
        result = await run_postgres_smoke(database_url)
    except (OSError, RuntimeError) as exc:
        pytest.skip(f"PostgreSQL is unavailable: {exc}")
    assert result["status"] == "passed"
    assert result["decision"] == "accepted"
