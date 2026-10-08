"""Durable source-event snapshots for crash-safe V3 processing recovery."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SourceEventSnapshot

from .types import SourceEvent


class SourceEventSnapshotError(RuntimeError):
    """Raised when a durable source-event snapshot cannot be restored."""


class SourceEventSnapshotStore:
    async def persist(
        self,
        session: AsyncSession,
        *,
        event: SourceEvent,
        event_key: str,
    ) -> SourceEventSnapshot:
        existing = await session.scalar(
            select(SourceEventSnapshot).where(
                SourceEventSnapshot.account_id == event.account_id,
                SourceEventSnapshot.source_id == event.source_id,
                SourceEventSnapshot.event_key == event_key,
            )
        )
        if existing is not None:
            return existing

        snapshot = SourceEventSnapshot(
            account_id=event.account_id,
            source_id=event.source_id,
            event_key=event_key,
            chat_id=event.chat_id,
            cursor_message_id=event.cursor_message_id,
            primary_message_id=event.primary_message_id,
            grouped_id=event.grouped_id,
            received_at=event.received_at,
            messages_json=event.to_snapshot_messages(),
        )
        session.add(snapshot)
        await session.flush()
        return snapshot

    async def restore(
        self,
        session: AsyncSession,
        *,
        account_id: uuid.UUID,
        source_id: uuid.UUID,
        event_key: str,
    ) -> SourceEvent:
        snapshot = await session.scalar(
            select(SourceEventSnapshot).where(
                SourceEventSnapshot.account_id == account_id,
                SourceEventSnapshot.source_id == source_id,
                SourceEventSnapshot.event_key == event_key,
            )
        )
        if snapshot is None:
            raise SourceEventSnapshotError("source_event_snapshot_missing")
        if not isinstance(snapshot.messages_json, list):
            raise SourceEventSnapshotError("source_event_snapshot_messages_invalid")

        event = SourceEvent.from_snapshot(
            account_id=account_id,
            source_id=source_id,
            chat_id=snapshot.chat_id,
            messages=snapshot.messages_json,
            received_at=snapshot.received_at,
        )
        if event.cursor_message_id != snapshot.cursor_message_id:
            raise SourceEventSnapshotError("source_event_snapshot_cursor_mismatch")
        return event
