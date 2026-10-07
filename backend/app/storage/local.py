"""Persistent local media materialization for live Telegram content."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import mimetypes
import os
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import ContentItem, ContentMedia, MediaStatus, RetentionMode
from app.telegram.v2_listener import IngestionEvent


class MediaMaterializationError(RuntimeError):
    """Raised when media cannot be materialized or recovered."""


class LocalMediaStore:
    """Store accepted Telegram media on the persistent runtime volume.

    The publisher still has a Telegram live-fetch fallback, so a transient
    materialization failure does not make a live message unrecoverable.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        client_manager: Any,
        account_id: uuid.UUID,
        *,
        root: str | Path,
    ) -> None:
        self.session_factory = session_factory
        self.client_manager = client_manager
        self.account_id = account_id
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.root, 0o700)
        except OSError:
            pass
        self._logger = logging.getLogger(__name__)

    async def materialize(self, content_item_id: uuid.UUID, event: IngestionEvent) -> int:
        """Download media for an accepted event and persist storage keys."""
        async with self.session_factory() as session:
            media_rows = list(
                (
                    await session.scalars(
                        select(ContentMedia)
                        .where(ContentMedia.content_item_id == content_item_id)
                        .order_by(ContentMedia.created_at, ContentMedia.id)
                    )
                ).all()
            )
        if not media_rows:
            return 0

        messages = tuple(getattr(event.message, "messages", ()) or ())
        if not messages:
            messages = (event.message,)
        client = await self.client_manager.ensure_connected()
        stored = 0

        for index, media_row in enumerate(media_rows):
            source_message = messages[min(index, len(messages) - 1)]
            try:
                payload = await client.download_media(source_message, file=bytes)
                if not payload:
                    raise MediaMaterializationError("Telegram returned an empty media payload")
                suffix = self._suffix(media_row)
                directory = self.root / str(self.account_id) / str(content_item_id)
                await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=True)
                path = directory / f"{media_row.id}{suffix}"
                await asyncio.to_thread(path.write_bytes, payload)
                checksum = hashlib.sha256(payload).hexdigest()
                async with self.session_factory() as session:
                    async with session.begin():
                        row = await session.get(ContentMedia, media_row.id)
                        if row is None:
                            continue
                        row.storage_key = str(path)
                        row.byte_size = len(payload)
                        row.checksum_sha256 = checksum
                        row.status = MediaStatus.STORED
                stored += 1
            except Exception as exc:
                self._logger.warning(
                    "v2 media materialization failed: content_item_id=%s media_id=%s error=%s",
                    content_item_id,
                    media_row.id,
                    type(exc).__name__,
                )
                async with self.session_factory() as session:
                    async with session.begin():
                        row = await session.get(ContentMedia, media_row.id)
                        if row is not None:
                            row.status = MediaStatus.FAILED
                            metadata = dict(row.metadata_json or {})
                            metadata["materialization_error"] = type(exc).__name__
                            row.metadata_json = metadata
        return stored

    async def cleanup_if_ephemeral(self, content_item_id: uuid.UUID) -> None:
        """Delete media only when the content retention policy is live-only."""
        async with self.session_factory() as session:
            item = await session.get(ContentItem, content_item_id)
            if item is None or item.retention_mode is not RetentionMode.NONE:
                return
        await self.cleanup_content(content_item_id)

    async def cleanup_content(self, content_item_id: uuid.UUID) -> None:
        """Delete local media for one content item."""
        async with self.session_factory() as session:
            rows = list(
                (
                    await session.scalars(
                        select(ContentMedia).where(ContentMedia.content_item_id == content_item_id)
                    )
                ).all()
            )
        parents: set[Path] = set()
        for row in rows:
            if not row.storage_key:
                continue
            path = Path(row.storage_key)
            try:
                await asyncio.to_thread(path.unlink, missing_ok=True)
                parents.add(path.parent)
            except OSError:
                self._logger.warning("failed to clean media file: %s", path)
        for parent in parents:
            try:
                await asyncio.to_thread(parent.rmdir)
            except OSError:
                pass

    @staticmethod
    def _suffix(row: ContentMedia) -> str:
        if row.original_file_name:
            suffix = Path(row.original_file_name).suffix
            if suffix and len(suffix) <= 12:
                return suffix
        if row.mime_type:
            guessed = mimetypes.guess_extension(row.mime_type)
            if guessed:
                return guessed
        return ".bin"
