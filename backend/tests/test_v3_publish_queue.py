from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.v3.content import ContentNormalizerV3, RouteContentPolicy
from app.v3.publish_queue import retry_delay_seconds
from app.v3.telegram.types import SourceEvent


def test_retry_backoff_grows_exponentially_and_is_capped():
    assert retry_delay_seconds(attempt_count=1, base_seconds=30, cap_seconds=300) == 30
    assert retry_delay_seconds(attempt_count=2, base_seconds=30, cap_seconds=300) == 60
    assert retry_delay_seconds(attempt_count=4, base_seconds=30, cap_seconds=300) == 240
    assert retry_delay_seconds(attempt_count=5, base_seconds=30, cap_seconds=300) == 300
    assert retry_delay_seconds(attempt_count=9, base_seconds=30, cap_seconds=300) == 300


def test_retry_after_is_a_minimum_even_above_normal_backoff_cap():
    assert (
        retry_delay_seconds(
            attempt_count=3,
            base_seconds=30,
            cap_seconds=300,
            retry_after_seconds=900,
        )
        == 900
    )


def test_retry_after_does_not_shorten_normal_backoff():
    assert (
        retry_delay_seconds(
            attempt_count=4,
            base_seconds=30,
            cap_seconds=300,
            retry_after_seconds=60,
        )
        == 240
    )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"attempt_count": 0, "base_seconds": 30, "cap_seconds": 300}, "attempt_count"),
        ({"attempt_count": 1, "base_seconds": 0, "cap_seconds": 300}, "base_seconds"),
        ({"attempt_count": 1, "base_seconds": 30, "cap_seconds": 10}, "cap_seconds"),
        (
            {
                "attempt_count": 1,
                "base_seconds": 30,
                "cap_seconds": 300,
                "retry_after_seconds": 0,
            },
            "retry_after_seconds",
        ),
    ],
)
def test_retry_contract_rejects_invalid_values(kwargs, message):
    with pytest.raises(ValueError, match=message):
        retry_delay_seconds(**kwargs)



def test_source_event_snapshot_roundtrip_preserves_processing_inputs():
    account_id = uuid.uuid4()
    source_id = uuid.uuid4()
    message = SimpleNamespace(
        id=77,
        grouped_id=None,
        date=datetime(2026, 10, 8, 2, 30, tzinfo=UTC),
        raw_text="خبر بصورة ✅",
        media=SimpleNamespace(
            media_type="photo",
            id=9001,
            mime_type="image/jpeg",
            size=1234,
        ),
    )
    event = SourceEvent.from_messages(
        account_id=account_id,
        source_id=source_id,
        chat_id=-100123,
        messages=(message,),
    )
    restored = SourceEvent.from_snapshot(
        account_id=account_id,
        source_id=source_id,
        chat_id=event.chat_id,
        messages=event.to_snapshot_messages(),
        received_at=event.received_at,
    )
    normalizer = ContentNormalizerV3()
    live = normalizer.normalize(event, RouteContentPolicy())
    recovered = normalizer.normalize(restored, RouteContentPolicy())

    assert restored.message_ids == event.message_ids
    assert restored.grouped_id == event.grouped_id
    assert recovered.normalized_text == live.normalized_text
    assert recovered.content_type == live.content_type
    assert recovered.media == live.media
