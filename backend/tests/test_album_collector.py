from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.models import Source
from app.relay.albums import AlbumCollector


def source() -> Source:
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    return Source(1, -1001, "@source", "Source", "source", True, 0, now, now)


def test_album_collector_emits_one_batch():
    async def scenario():
        batches = []

        async def on_album(_source, messages):
            batches.append([message.id for message in messages])

        collector = AlbumCollector(on_album, window_seconds=0.01)
        await collector.add(source(), SimpleNamespace(id=1, grouped_id=7))
        await collector.add(source(), SimpleNamespace(id=2, grouped_id=7))
        await asyncio.sleep(0.03)
        assert batches == [[1, 2]]
        await collector.close()

    asyncio.run(scenario())


def test_album_collector_flushes_pending_on_close():
    async def scenario():
        batches = []

        async def on_album(_source, messages):
            batches.append([message.id for message in messages])

        collector = AlbumCollector(on_album, window_seconds=10)
        await collector.add(source(), SimpleNamespace(id=3, grouped_id=9))
        await collector.close()
        assert batches == [[3]]

    asyncio.run(scenario())
