"""Preflight checks for the real V3 Telegram pilot.

The command reports only readiness/status information and never prints credentials.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy import func, select

from app.db import Database
from app.db.models import (
    Account,
    Destination,
    Project,
    Source,
    SourceRoute,
    TelegramAccount,
)
from app.v3.config import RuntimeV3Settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate V3 pilot readiness")
    parser.add_argument("--env-file", default=".env")
    return parser


async def run(env_file: str | Path | None) -> dict[str, object]:
    settings = RuntimeV3Settings.from_env(env_file, require_telegram=True)
    if settings.ingestion is None or settings.telegram is None:
        raise RuntimeError("V3 Telegram ingestion settings are required")

    database = Database(settings.database)
    try:
        database_ready = await database.ping()
        async with database.session() as session:
            account = await session.get(Account, settings.ingestion.account_id)
            telegram = await session.get(
                TelegramAccount,
                settings.ingestion.telegram_account_id,
            )
            if telegram is not None and telegram.account_id != settings.ingestion.account_id:
                telegram = None

            project_count = int(
                (
                    await session.scalar(
                        select(func.count())
                        .select_from(Project)
                        .where(Project.account_id == settings.ingestion.account_id)
                    )
                )
                or 0
            )
            source_count = int(
                (
                    await session.scalar(
                        select(func.count())
                        .select_from(Source)
                        .where(Source.account_id == settings.ingestion.account_id)
                    )
                )
                or 0
            )
            destination_count = int(
                (
                    await session.scalar(
                        select(func.count())
                        .select_from(Destination)
                        .where(Destination.account_id == settings.ingestion.account_id)
                    )
                )
                or 0
            )
            route_count = int(
                (
                    await session.scalar(
                        select(func.count())
                        .select_from(SourceRoute)
                        .where(SourceRoute.account_id == settings.ingestion.account_id)
                    )
                )
                or 0
            )
    finally:
        await database.close()

    session_path = settings.telegram.session_path
    session_present = session_path.is_file()
    publisher_enabled = settings.publisher is not None
    control_enabled = settings.control is not None

    ready = all(
        (
            database_ready,
            account is not None,
            telegram is not None,
            session_present,
            publisher_enabled,
            control_enabled,
        )
    )
    return {
        "ready": ready,
        "database_ready": database_ready,
        "account_ready": account is not None,
        "telegram_account_ready": telegram is not None,
        "session_file_present": session_present,
        "publisher_enabled": publisher_enabled,
        "control_enabled": control_enabled,
        "project_count": project_count,
        "source_count": source_count,
        "destination_count": destination_count,
        "route_count": route_count,
    }


def main() -> None:
    args = build_parser().parse_args()
    print(json.dumps(asyncio.run(run(args.env_file)), sort_keys=True))


if __name__ == "__main__":
    main()
