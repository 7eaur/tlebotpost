"""Importer from the legacy SQLite configuration into PostgreSQL v2.

Only configuration and live cursors are migrated. Message text and media are
intentionally not read or written because the legacy system is live-only.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import aiosqlite
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .connection import Database
from .models import (
    Account,
    BrandingProfile,
    DeduplicationProfile,
    Destination,
    DestinationStatus,
    FilterProfile,
    Project,
    PublishingMode,
    RetentionMode,
    RetentionPolicy,
    RouteStatus,
    ScheduleKind,
    ScheduleProfile,
    Source,
    SourceCheckpoint,
    SourceRoute,
    SourceStatus,
    TelegramAccount,
    TelegramAccountStatus,
    TransformProfile,
)


class LegacyImportError(RuntimeError):
    """Raised when the legacy database cannot be safely imported."""


@dataclass(frozen=True, slots=True)
class LegacySourceRecord:
    chat_id: int
    input_ref: str
    title: str
    username: str | None
    enabled: bool
    baseline_message_id: int


@dataclass(frozen=True, slots=True)
class LegacySettingsRecord:
    target_ref: str | None
    target_chat_id: int | None
    brand_footer: str
    brand_link: str
    enabled: bool
    include_keywords: tuple[str, ...]
    exclude_keywords: tuple[str, ...]
    allowed_media_types: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LegacySnapshot:
    settings: LegacySettingsRecord
    sources: tuple[LegacySourceRecord, ...]


@dataclass(slots=True)
class ImportReport:
    account_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    destination_id: uuid.UUID | None = None
    telegram_account_id: uuid.UUID | None = None
    source_ids: list[uuid.UUID] = field(default_factory=list)
    route_ids: list[uuid.UUID] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    dry_run: bool = False
    migrated_message_count: int = 0
    migrated_media_count: int = 0

    @property
    def source_count(self) -> int:
        return len(self.source_ids)

    @property
    def route_count(self) -> int:
        return len(self.route_ids)


async def load_legacy_snapshot(path: str | Path) -> LegacySnapshot:
    """Read only the legacy settings and source configuration tables."""
    database_path = Path(path)
    if not database_path.exists():
        raise LegacyImportError(f"legacy SQLite database does not exist: {database_path}")

    async with aiosqlite.connect(database_path) as connection:
        connection.row_factory = aiosqlite.Row
        tables = {
            row[0]
            for row in await (
                await connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            ).fetchall()
        }
        required = {"settings", "sources"}
        missing = required - tables
        if missing:
            raise LegacyImportError(f"legacy database is missing tables: {sorted(missing)}")

        settings_cursor = await connection.execute("SELECT * FROM settings WHERE id = 1")
        settings_row = await settings_cursor.fetchone()
        if settings_row is None:
            raise LegacyImportError("legacy settings singleton row is missing")

        source_cursor = await connection.execute("SELECT * FROM sources ORDER BY id")
        source_rows = await source_cursor.fetchall()

    settings = LegacySettingsRecord(
        target_ref=settings_row["target_ref"],
        target_chat_id=settings_row["target_chat_id"],
        brand_footer=settings_row["brand_footer"] or "",
        brand_link=settings_row["brand_link"] or "",
        enabled=bool(settings_row["enabled"]),
        include_keywords=_json_string_tuple(settings_row["include_keywords"], "include_keywords"),
        exclude_keywords=_json_string_tuple(settings_row["exclude_keywords"], "exclude_keywords"),
        allowed_media_types=_json_string_tuple(
            settings_row["allowed_media_types"], "allowed_media_types"
        ),
    )
    sources = tuple(
        LegacySourceRecord(
            chat_id=row["chat_id"],
            input_ref=row["input_ref"],
            title=row["title"],
            username=row["username"],
            enabled=bool(row["enabled"]),
            baseline_message_id=row["baseline_message_id"],
        )
        for row in source_rows
    )
    return LegacySnapshot(settings=settings, sources=sources)


async def import_legacy_snapshot(
    database: Database,
    snapshot: LegacySnapshot,
    *,
    account_name: str = "Migrated account",
    account_slug: str = "legacy",
    project_name: str = "Legacy project",
    project_slug: str = "legacy",
    telegram_account_label: str = "legacy-user",
    telegram_session_key: str = "legacy-user",
    dry_run: bool = False,
) -> ImportReport:
    """Import a previously loaded snapshot in one PostgreSQL transaction."""
    report = ImportReport(dry_run=dry_run)
    if snapshot.settings.target_chat_id is None:
        report.warnings.append("legacy target is not configured; no destination will be created")
    if not snapshot.settings.brand_footer and not snapshot.settings.brand_link:
        report.warnings.append("legacy branding is empty; a blank branding profile will be used")
    if dry_run:
        report.warnings.extend(_dry_run_warnings(snapshot))
        return report

    async with database.transaction() as session:
        account = await _get_or_create(
            session,
            Account,
            {"slug": account_slug},
            {"name": account_name, "status": "active"},
        )
        report.account_id = account.id

        project = await _get_or_create(
            session,
            Project,
            {"account_id": account.id, "slug": project_slug},
            {"name": project_name, "status": "active"},
        )
        report.project_id = project.id

        telegram_account = await _get_or_create(
            session,
            TelegramAccount,
            {"account_id": account.id, "session_key": telegram_session_key},
            {
                "label": telegram_account_label,
                "status": TelegramAccountStatus.DISCONNECTED,
            },
        )
        report.telegram_account_id = telegram_account.id

        branding = await _get_or_create(
            session,
            BrandingProfile,
            {"account_id": account.id, "name": "Imported legacy branding"},
            {
                "footer": snapshot.settings.brand_footer,
                "link": snapshot.settings.brand_link,
                "separator": None,
                "enabled": True,
            },
        )
        filters = await _get_or_create(
            session,
            FilterProfile,
            {"account_id": account.id, "name": "Imported legacy filters"},
            {
                "include_keywords": list(snapshot.settings.include_keywords),
                "exclude_keywords": list(snapshot.settings.exclude_keywords),
                "allowed_media_types": list(snapshot.settings.allowed_media_types),
                "remove_urls": True,
                "remove_source_rights": True,
                "preserve_emoji": True,
            },
        )
        transform = await _get_or_create(
            session,
            TransformProfile,
            {"account_id": account.id, "name": "Imported legacy transform"},
            {"remove_link_preview": True, "preserve_line_breaks": True, "trim_whitespace": True},
        )
        deduplication = await _get_or_create(
            session,
            DeduplicationProfile,
            {"account_id": account.id, "name": "Imported legacy deduplication"},
            {"enabled": True, "window_seconds": 86400},
        )
        schedule = await _get_or_create(
            session,
            ScheduleProfile,
            {"account_id": account.id, "name": "Imported legacy direct publishing"},
            {"kind": ScheduleKind.IMMEDIATE, "timezone": "UTC"},
        )
        retention = await _get_or_create(
            session,
            RetentionPolicy,
            {"account_id": account.id, "name": "Imported legacy live-only retention"},
            {"mode": RetentionMode.NONE},
        )

        destination = None
        if snapshot.settings.target_chat_id is not None:
            destination = await _get_or_create(
                session,
                Destination,
                {
                    "account_id": account.id,
                    "telegram_chat_id": snapshot.settings.target_chat_id,
                },
                {
                    "project_id": project.id,
                    "name": snapshot.settings.target_ref or "Legacy target",
                    "telegram_username": snapshot.settings.target_ref,
                    "status": (
                        DestinationStatus.ACTIVE
                        if snapshot.settings.enabled
                        else DestinationStatus.PAUSED
                    ),
                    "publishing_mode": PublishingMode.DIRECT,
                    "branding_profile_id": branding.id,
                    "deduplication_profile_id": deduplication.id,
                    "schedule_profile_id": schedule.id,
                    "retention_policy_id": retention.id,
                },
            )
            report.destination_id = destination.id

        for record in snapshot.sources:
            source = await _get_or_create(
                session,
                Source,
                {
                    "account_id": account.id,
                    "telegram_account_id": telegram_account.id,
                    "telegram_chat_id": record.chat_id,
                },
                {
                    "telegram_username": record.username,
                    "title": record.title,
                    "status": SourceStatus.ACTIVE if record.enabled else SourceStatus.PAUSED,
                },
            )
            report.source_ids.append(source.id)
            await _get_or_create(
                session,
                SourceCheckpoint,
                {"source_id": source.id},
                {"last_seen_message_id": record.baseline_message_id},
            )
            if destination is not None:
                route = await _get_or_create(
                    session,
                    SourceRoute,
                    {"source_id": source.id, "destination_id": destination.id},
                    {
                        "account_id": account.id,
                        "status": (
                            RouteStatus.ACTIVE
                            if record.enabled and snapshot.settings.enabled
                            else RouteStatus.PAUSED
                        ),
                        "publishing_mode": PublishingMode.DIRECT,
                        "filter_profile_id": filters.id,
                        "transform_profile_id": transform.id,
                        "branding_profile_id": branding.id,
                        "schedule_profile_id": schedule.id,
                        "deduplication_profile_id": deduplication.id,
                        "retention_policy_id": retention.id,
                    },
                )
                report.route_ids.append(route.id)

    return report


async def import_legacy_database(
    database: Database,
    sqlite_path: str | Path,
    **kwargs: object,
) -> ImportReport:
    """Load a legacy SQLite file and import it into PostgreSQL."""
    snapshot = await load_legacy_snapshot(sqlite_path)
    return await import_legacy_snapshot(database, snapshot, **kwargs)


async def _get_or_create(
    session: AsyncSession,
    model: type,
    identity: dict[str, object],
    values: dict[str, object],
):
    filters = [getattr(model, key) == value for key, value in identity.items()]
    instance = await session.scalar(select(model).where(*filters))
    if instance is not None:
        return instance
    instance = model(**identity, **values)
    session.add(instance)
    await session.flush()
    return instance


def _json_string_tuple(value: str | None, field: str) -> tuple[str, ...]:
    if not value:
        return ()
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise LegacyImportError(f"legacy {field} contains invalid JSON") from exc
    if not isinstance(decoded, list) or not all(isinstance(item, str) for item in decoded):
        raise LegacyImportError(f"legacy {field} must be a JSON string array")
    return tuple(dict.fromkeys(item.strip() for item in decoded if item.strip()))


def _dry_run_warnings(snapshot: LegacySnapshot) -> list[str]:
    warnings = [
        f"would import {len(snapshot.sources)} source configuration(s)",
        "would import zero content items and zero media files by design",
    ]
    if snapshot.settings.target_chat_id is not None:
        warnings.append("would create one direct-publishing destination")
    return warnings
