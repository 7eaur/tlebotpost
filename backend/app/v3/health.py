"""Health check for the V3 foundation."""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.db import Database

from .config import RuntimeV3Settings


async def check(env_file: str | None = ".env") -> None:
    settings = RuntimeV3Settings.from_env(env_file, require_telegram=False)
    database = Database(settings.database)
    try:
        if not await database.ping():
            raise RuntimeError("PostgreSQL readiness check failed")
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Check V3 PostgreSQL readiness")
    parser.add_argument("--env-file", default=".env")
    args = parser.parse_args()
    try:
        asyncio.run(check(args.env_file))
    except Exception as exc:
        print(f"v3 healthcheck failed: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
