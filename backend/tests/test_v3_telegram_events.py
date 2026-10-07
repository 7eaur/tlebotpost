from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.v3.telegram.albums import AlbumCollectorV3, AlbumSource
from app.v3.telegram.types import SourceEvent


def message(message_id: int, *, grouped_id: int | None = None):
    return SimpleNamespace(
        id=message_id,
        grouped_id=grouped_id,
        date=datetime(2026, 10, 7, 21, 0, message_id % 60, tzinfo=UTC),
    )


def test_source_event_sorts_album_and_uses_highest_cursor():
    account_id = uuid.uuid4()
    source_id = uuid.uuid4()

    event = SourceEvent.from_messages(
        account_id=account_id,
        source_id=source_id,
        chat_id=-1001,
        messages=(message(12, grouped_id=77), message(11, grouped_id=77)),
    )

    assert event.message_ids == (11, 12)
    assert event.primary_message_id == 11
    assert event.cursor_message_id == 12
    assert event.grouped_id == 77


def test_source_event_rejects_mixed_group_identity():
    with pytest.raises(ValueError, match="share one grouped_id"):
        SourceEvent.from_messages(
            account_id=uuid.uuid4(),
            source_id=uuid.uuid4(),
            chat_id=-1001,
            messages=(message(11, grouped_id=77), message(12, grouped_id=88)),
        )


@pytest.mark.asyncio
async def test_album_collector_preserves_source_order_and_deduplicates_parts():
    account_id = uuid.uuid4()
    source_id = uuid.uuid4()
    source = AlbumSource(account_id=account_id, source_id=source_id, chat_id=-1001)
    received: list[SourceEvent] = []

    async def on_event(event: SourceEvent) -> None:
        received.append(event)

    collector = AlbumCollectorV3(on_event, window_seconds=60)
    await collector.add(source, message(11, grouped_id=77))
    await collector.add(source, message(11, grouped_id=77))
    await collector.add(source, message(12, grouped_id=77))
    await collector.add(source, message(13))

    assert [event.message_ids for event in received] == [(11, 12), (13,)]
    assert [event.cursor_message_id for event in received] == [12, 13]
    await collector.close()


@pytest.mark.asyncio
async def test_album_collector_flushes_pending_on_close():
    source = AlbumSource(
        account_id=uuid.uuid4(),
        source_id=uuid.uuid4(),
        chat_id=-1001,
    )
    received: list[SourceEvent] = []

    async def on_event(event: SourceEvent) -> None:
        received.append(event)

    collector = AlbumCollectorV3(on_event, window_seconds=60)
    await collector.add(source, message(21, grouped_id=90))
    await collector.add(source, message(22, grouped_id=90))
    await collector.close()

    assert len(received) == 1
    assert received[0].message_ids == (21, 22)
