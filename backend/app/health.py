"""Container health check for the relay service."""

from __future__ import annotations

import asyncio
import sys

from app.config import Settings
from app.database import Database


async def check() -> None:
    settings = Settings.from_env()
    database = Database(settings.database_path)
    await database.initialize()
    async with database.connection() as connection:
        cursor = await connection.execute("SELECT 1 FROM settings WHERE id = 1")
        if await cursor.fetchone() is None:
            raise RuntimeError("settings row is missing")
    await database.close()


if __name__ == "__main__":
    try:
        asyncio.run(check())
    except Exception as exc:
        print(f"healthcheck failed: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1) from exc
