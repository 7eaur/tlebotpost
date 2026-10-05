"""Persistence operations for the singleton relay settings row."""

from __future__ import annotations

import json

from app.database import Database, utc_now
from app.models import RelaySettings


class SettingsRepository:
    """Read and update the one configuration record used by the relay."""

    def __init__(self, database: Database) -> None:
        self.database = database

    async def get(self) -> RelaySettings:
        """Return settings, relying on schema initialization for the singleton row."""
        async with self.database.connection() as connection:
            cursor = await connection.execute("SELECT * FROM settings WHERE id = 1")
            row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("settings table is not initialized")
        return _settings_from_row(row)

    async def update_target(
        self, target_ref: str | None, target_chat_id: int | None
    ) -> RelaySettings:
        """Set or clear the target reference and resolved Telegram id."""
        async with self.database.connection() as connection:
            await connection.execute(
                """
                UPDATE settings
                   SET target_ref = ?, target_chat_id = ?, updated_at = ?
                 WHERE id = 1
                """,
                (target_ref, target_chat_id, utc_now()),
            )
            await connection.commit()
        return await self.get()

    async def update_branding(self, footer: str, link: str) -> RelaySettings:
        """Set the fixed ownership footer and link used by the transformer."""
        if not footer.strip() and not link.strip():
            raise ValueError("footer or link must be configured")
        async with self.database.connection() as connection:
            await connection.execute(
                """
                UPDATE settings
                   SET brand_footer = ?, brand_link = ?, updated_at = ?
                 WHERE id = 1
                """,
                (footer.strip(), link.strip(), utc_now()),
            )
            await connection.commit()
        return await self.get()

    async def update_filters(
        self,
        *,
        include_keywords: tuple[str, ...],
        exclude_keywords: tuple[str, ...],
        allowed_media_types: tuple[str, ...],
    ) -> RelaySettings:
        """Persist normalized filter values as JSON arrays."""
        values = [
            _normalize_items(include_keywords),
            _normalize_items(exclude_keywords),
            _normalize_items(allowed_media_types),
        ]
        async with self.database.connection() as connection:
            await connection.execute(
                """
                UPDATE settings
                   SET include_keywords = ?, exclude_keywords = ?,
                       allowed_media_types = ?, updated_at = ?
                 WHERE id = 1
                """,
                (
                    json.dumps(values[0], ensure_ascii=False),
                    json.dumps(values[1], ensure_ascii=False),
                    json.dumps(values[2], ensure_ascii=False),
                    utc_now(),
                ),
            )
            await connection.commit()
        return await self.get()

    async def set_enabled(self, enabled: bool) -> RelaySettings:
        """Enable or pause the relay globally."""
        async with self.database.connection() as connection:
            await connection.execute(
                "UPDATE settings SET enabled = ?, updated_at = ? WHERE id = 1",
                (int(enabled), utc_now()),
            )
            await connection.commit()
        return await self.get()

    async def sync_environment_defaults(
        self,
        *,
        brand_footer: str,
        brand_link: str,
        include_keywords: tuple[str, ...],
        exclude_keywords: tuple[str, ...],
    ) -> RelaySettings:
        """Fill only empty database defaults; never overwrite bot-managed values."""
        current = await self.get()
        if not current.brand_footer and not current.brand_link:
            current = await self.update_branding(brand_footer, brand_link)
        if not current.include_keywords and include_keywords:
            current = await self.update_filters(
                include_keywords=include_keywords,
                exclude_keywords=current.exclude_keywords or exclude_keywords,
                allowed_media_types=current.allowed_media_types,
            )
        elif not current.exclude_keywords and exclude_keywords:
            current = await self.update_filters(
                include_keywords=current.include_keywords,
                exclude_keywords=exclude_keywords,
                allowed_media_types=current.allowed_media_types,
            )
        return current


def _normalize_items(items: tuple[str, ...]) -> list[str]:
    return list(dict.fromkeys(item.strip() for item in items if item.strip()))


def _json_tuple(value: str) -> tuple[str, ...]:
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise RuntimeError("invalid JSON in settings") from exc
    if not isinstance(decoded, list) or not all(isinstance(item, str) for item in decoded):
        raise RuntimeError("settings filter must be a JSON string array")
    return tuple(decoded)


def _settings_from_row(row: object) -> RelaySettings:
    return RelaySettings(
        target_ref=row["target_ref"],
        target_chat_id=row["target_chat_id"],
        brand_footer=row["brand_footer"],
        brand_link=row["brand_link"],
        enabled=bool(row["enabled"]),
        include_keywords=_json_tuple(row["include_keywords"]),
        exclude_keywords=_json_tuple(row["exclude_keywords"]),
        allowed_media_types=_json_tuple(row["allowed_media_types"]),
    )
