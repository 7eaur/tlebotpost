from __future__ import annotations

import uuid

from app.db.models import ContentType
from app.v3.content import NormalizedMedia, ProcessedContent
from app.v3.deduplication import (
    DeduplicationPolicy,
    DeduplicationScope,
    FingerprintBuilderV3,
    FingerprintType,
)


def content(text: str = "", *media_ids: str) -> ProcessedContent:
    media = tuple(
        NormalizedMedia(
            media_type="photo",
            identity=identity,
            source_message_id=index + 1,
        )
        for index, identity in enumerate(media_ids)
    )
    return ProcessedContent(
        normalized_text=text,
        rendered_text=text,
        content_type=ContentType.PHOTO if media else ContentType.TEXT,
        media=media,
    )


def kinds(signals):
    return {signal.kind for signal in signals}


def test_text_only_never_emits_empty_media_fingerprint():
    signals = FingerprintBuilderV3.build(
        source_id=uuid.uuid4(),
        event_key="message:1",
        content=content("خبر أول"),
        policy=DeduplicationPolicy(),
    )

    assert FingerprintType.TEXT in kinds(signals)
    assert FingerprintType.MEDIA not in kinds(signals)
    assert FingerprintType.COMBINED in kinds(signals)


def test_media_only_never_emits_empty_text_fingerprint():
    signals = FingerprintBuilderV3.build(
        source_id=uuid.uuid4(),
        event_key="message:2",
        content=content("", "photo:101"),
        policy=DeduplicationPolicy(),
    )

    assert FingerprintType.TEXT not in kinds(signals)
    assert FingerprintType.MEDIA in kinds(signals)
    assert FingerprintType.COMBINED in kinds(signals)


def test_empty_content_with_identity_disabled_emits_no_fingerprints():
    signals = FingerprintBuilderV3.build(
        source_id=uuid.uuid4(),
        event_key="message:3",
        content=content(""),
        policy=DeduplicationPolicy(compare_telegram_identity=False),
    )

    assert signals == ()


def test_different_text_produces_different_text_and_combined_hashes():
    source_id = uuid.uuid4()
    first = FingerprintBuilderV3.build(
        source_id=source_id,
        event_key="message:4",
        content=content("الأول"),
        policy=DeduplicationPolicy(compare_telegram_identity=False),
    )
    second = FingerprintBuilderV3.build(
        source_id=source_id,
        event_key="message:5",
        content=content("الثاني"),
        policy=DeduplicationPolicy(compare_telegram_identity=False),
    )

    first_map = {item.kind: item.value for item in first}
    second_map = {item.kind: item.value for item in second}
    assert first_map[FingerprintType.TEXT] != second_map[FingerprintType.TEXT]
    assert first_map[FingerprintType.COMBINED] != second_map[FingerprintType.COMBINED]


def test_media_order_is_part_of_album_fingerprint():
    policy = DeduplicationPolicy(compare_telegram_identity=False, compare_text=False)
    source_id = uuid.uuid4()
    first = FingerprintBuilderV3.build(
        source_id=source_id,
        event_key="group:10",
        content=content("", "photo:a", "photo:b"),
        policy=policy,
    )
    second = FingerprintBuilderV3.build(
        source_id=source_id,
        event_key="group:11",
        content=content("", "photo:b", "photo:a"),
        policy=policy,
    )

    first_media = next(item.value for item in first if item.kind is FingerprintType.MEDIA)
    second_media = next(item.value for item in second if item.kind is FingerprintType.MEDIA)
    assert first_media != second_media


def test_default_scope_is_route_and_destination_scope_is_explicit():
    assert DeduplicationPolicy().scope is DeduplicationScope.ROUTE
    assert DeduplicationPolicy(scope=DeduplicationScope.DESTINATION).scope is (
        DeduplicationScope.DESTINATION
    )


def test_disabled_policy_emits_no_fingerprints():
    signals = FingerprintBuilderV3.build(
        source_id=uuid.uuid4(),
        event_key="message:6",
        content=content("خبر"),
        policy=DeduplicationPolicy(enabled=False),
    )

    assert signals == ()
