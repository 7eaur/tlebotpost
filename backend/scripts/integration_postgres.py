#!/usr/bin/env python3
"""Run the PostgreSQL-backed v2 integration smoke test in Docker."""

from __future__ import annotations

import asyncio
import json
import os
import sys

from app.integration_smoke import run_postgres_smoke


async def main() -> None:
    database_url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("TEST_DATABASE_URL or DATABASE_URL is required")
    result = await run_postgres_smoke(database_url)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(f"PostgreSQL integration smoke test failed: {exc}", file=sys.stderr)
        raise
