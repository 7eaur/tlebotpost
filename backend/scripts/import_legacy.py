#!/usr/bin/env python3
"""Import legacy SQLite configuration into the PostgreSQL v2 schema."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from app.db import Database
from app.db.importer import import_legacy_database, load_legacy_snapshot


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import legacy SQLite settings and sources into PostgreSQL v2"
    )
    parser.add_argument("--sqlite", required=True, type=Path, help="legacy SQLite database path")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="read and validate SQLite only; do not connect to or write PostgreSQL",
    )
    parser.add_argument("--account-name", default="Migrated account")
    parser.add_argument("--account-slug", default="legacy")
    parser.add_argument("--project-name", default="Legacy project")
    parser.add_argument("--project-slug", default="legacy")
    parser.add_argument("--telegram-account-label", default="legacy-user")
    parser.add_argument("--telegram-session-key", default="legacy-user")
    return parser


async def run(args: argparse.Namespace) -> dict[str, object]:
    if args.dry_run:
        snapshot = await load_legacy_snapshot(args.sqlite)
        return {
            "dry_run": True,
            "source_count": len(snapshot.sources),
            "has_destination": snapshot.settings.target_chat_id is not None,
            "message_count": 0,
            "media_count": 0,
            "warnings": [
                "No PostgreSQL connection was opened",
                "No content or media will be imported",
            ],
        }

    database = Database.from_env()
    try:
        report = await import_legacy_database(
            database,
            args.sqlite,
            account_name=args.account_name,
            account_slug=args.account_slug,
            project_name=args.project_name,
            project_slug=args.project_slug,
            telegram_account_label=args.telegram_account_label,
            telegram_session_key=args.telegram_session_key,
        )
        return {
            "dry_run": report.dry_run,
            "account_id": str(report.account_id),
            "project_id": str(report.project_id),
            "destination_id": str(report.destination_id) if report.destination_id else None,
            "telegram_account_id": str(report.telegram_account_id),
            "source_count": report.source_count,
            "route_count": report.route_count,
            "message_count": report.migrated_message_count,
            "media_count": report.migrated_media_count,
            "warnings": report.warnings,
        }
    finally:
        await database.close()


def main() -> None:
    args = build_parser().parse_args()
    try:
        print(json.dumps(asyncio.run(run(args)), ensure_ascii=False, indent=2))
    except Exception as exc:
        raise SystemExit(f"Import failed: {exc}") from exc


if __name__ == "__main__":
    main()
