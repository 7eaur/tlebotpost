"""Retention cleanup worker for Runtime v2."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import ContentItem, ContentMedia, ContentStatus, MediaStatus


class RetentionCleaner:
    """Delete expired media and scrub expired content payloads."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        account_id: uuid.UUID,
        *,
        batch_size: int = 100,
    ) -> None:
        self.session_factory = session_factory
        self.account_id = account_id
        self.batch_size = max(1, batch_size)
        self._logger = logging.getLogger(__name__)

    async def run_once(self) -> dict[str, int]:
        now = datetime.now(UTC)
        media_deleted = await self._cleanup_media(now)
        content_expired = await self._expire_content(now)
        return {
            "media_deleted": media_deleted,
            "content_expired": content_expired,
        }

    async def _cleanup_media(self, now: datetime) -> int:
        async with self.session_factory() as session:
            rows = list(
                (
                    await session.scalars(
                        select(ContentMedia)
                        .join(ContentItem, ContentItem.id == ContentMedia.content_item_id)
                        .where(
                            ContentItem.account_id == self.account_id,
                            ContentMedia.expires_at.is_not(None),
                            ContentMedia.expires_at <= now,
                            ContentMedia.status != MediaStatus.DELETED,
                        )
                        .limit(self.batch_size)
                    )
                ).all()
            )

        deleted = 0
        for row in rows:
            if row.storage_key:
                path = Path(row.storage_key)
                try:
                    await asyncio.to_thread(path.unlink, missing_ok=True)
                except OSError:
                    self._logger.warning("retention delete failed: %s", path)
                    continue
            async with self.session_factory() as session:
                async with session.begin():
                    current = await session.get(ContentMedia, row.id)
                    if current is None:
                        continue
                    current.storage_key = None
                    current.status = MediaStatus.DELETED
            deleted += 1
        return deleted

    async def _expire_content(self, now: datetime) -> int:
        terminal = {
            ContentStatus.PUBLISHED,
            ContentStatus.FAILED,
            ContentStatus.SKIPPED,
        }
        async with self.session_factory() as session:
            async with session.begin():
                rows = list(
                    (
                        await session.scalars(
                            select(ContentItem)
                            .where(
                                ContentItem.account_id == self.account_id,
                                ContentItem.expires_at.is_not(None),
                                ContentItem.expires_at <= now,
                                ContentItem.status.in_(terminal),
                            )
                            .limit(self.batch_size)
                            .with_for_update(skip_locked=True)
                        )
                    ).all()
                )
                for item in rows:
                    item.text_original = None
                    item.text_normalized = None
                    item.status = ContentStatus.EXPIRED
                return len(rows)
