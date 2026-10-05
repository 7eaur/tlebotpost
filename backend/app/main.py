"""Phase 1 entry point: validate settings and initialize SQLite."""

from __future__ import annotations

import asyncio
import logging

from .config import Settings
from .database import Database


async def run() -> None:
    settings = Settings.from_env()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(message)s")
    database = Database(settings.database_path)
    await database.initialize()
    logging.getLogger(__name__).info("SQLite initialized at %s", settings.database_path)
    await database.close()


if __name__ == "__main__":
    asyncio.run(run())
