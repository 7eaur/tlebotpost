"""Initialize a fresh V3 database and create the minimum runtime identity."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import asyncpg
from alembic import command
from alembic.config import Config

from app.db import Database, DatabaseSettings
from app.v3.bootstrap import bootstrap_v3_identity


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Initialize a fresh Telegram Relay V3 database without importing legacy data"
    )
    parser.add_argument("--account-name", default="Telegram Relay V3")
    parser.add_argument("--account-slug", default="runtime-v3")
    parser.add_argument("--project-name", default="Telegram Relay")
    parser.add_argument("--project-slug", default="relay")
    parser.add_argument("--telegram-label", default="primary")
    parser.add_argument("--session-key", default="v3-primary")
    return parser


async def _ensure_reference_schema(settings: DatabaseSettings) -> bool:
    dsn = settings.url.replace("postgresql+asyncpg://", "postgresql://", 1)
    connection = await asyncpg.connect(dsn)
    try:
        existing = await connection.fetchval("SELECT to_regclass('public.accounts')")
        if existing:
            return False
        schema_path = Path(__file__).resolve().parents[1] / "db" / "schema.sql"
        await connection.execute(schema_path.read_text(encoding="utf-8"))
        return True
    finally:
        await connection.close()


def _upgrade_to_head() -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    command.upgrade(config, "head")


async def run(args: argparse.Namespace) -> dict[str, object]:
    settings = DatabaseSettings.from_env()
    base_created = await _ensure_reference_schema(settings)
    await asyncio.to_thread(_upgrade_to_head)

    database = Database(settings)
    try:
        result = await bootstrap_v3_identity(
            database.session_factory,
            account_name=args.account_name,
            account_slug=args.account_slug,
            project_name=args.project_name,
            project_slug=args.project_slug,
            telegram_label=args.telegram_label,
            session_key=args.session_key,
        )
    finally:
        await database.close()

    return {
        "base_schema_created": base_created,
        "account_id": str(result.account_id),
        "project_id": str(result.project_id),
        "telegram_account_id": str(result.telegram_account_id),
        "created_account": result.created_account,
        "created_project": result.created_project,
        "created_telegram_account": result.created_telegram_account,
        "legacy_data_imported": False,
    }


def main() -> None:
    args = build_parser().parse_args()
    payload = asyncio.run(run(args))
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
