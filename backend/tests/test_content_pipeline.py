from __future__ import annotations

import uuid
from types import SimpleNamespace

from app.content import ContentNormalizer, FilterEngine, PipelineDecision
from app.content.pipeline import ContentPipeline
from app.db.models import DeduplicationProfile, FilterProfile
from app.telegram.v2_listener import IngestionEvent


def make_event(message):
    account_id = uuid.uuid4()
    return IngestionEvent(
        account_id=account_id,
        source_id=uuid.uuid4(),
        route_id=uuid.uuid4(),
        destination_id=uuid.uuid4(),
        chat_id=-1001,
        message_id=42,
        grouped_id=None,
        message=message,
        received_at=SimpleNamespace(),
    )


def test_normalizer_removes_telegram_rights_but_preserves_emoji_and_layout():
    message = SimpleNamespace(
        text="عاجل 🚨 الخبر المهم\nــــــــــــــــــــ\nحقوق المصدر https://t.me/source"
    )
    normalized = ContentNormalizer().normalize(message)
    assert normalized.normalized_text == "عاجل 🚨 الخبر المهم"
    assert normalized.content_type.value == "text"


def test_normalizer_extracts_media_without_downloading_it():
    message = SimpleNamespace(
        caption="صورة الخبر",
        media=SimpleNamespace(media_type="photo", file_unique_id="photo-1"),
    )
    normalized = ContentNormalizer().normalize(message)
    assert normalized.content_type.value == "photo"
    assert normalized.media[0].identity == "file_unique_id:photo-1"


def test_filter_engine_applies_include_exclude_and_media_rules():
    engine = FilterEngine()
    content = ContentNormalizer().normalize(SimpleNamespace(text="خبر عاجل"))
    profile = FilterProfile(
        include_keywords=["عاجل"],
        exclude_keywords=["محذوف"],
        allowed_media_types=[],
    )
    assert engine.apply(content, profile) is None

    profile.exclude_keywords = ["عاجل"]
    assert engine.apply(content, profile) == "matched_exclude_keyword"

    profile.exclude_keywords = []
    profile.include_keywords = ["رياضة"]
    assert engine.apply(content, profile) == "missing_include_keyword"


def test_filter_engine_rejects_disallowed_media():
    content = ContentNormalizer().normalize(
        SimpleNamespace(media=SimpleNamespace(media_type="video", id=1))
    )
    profile = FilterProfile(allowed_media_types=["photo"])
    assert FilterEngine().apply(content, profile) == "media_type_not_allowed"


def test_fingerprints_include_source_message_and_normalized_content():
    event = make_event(SimpleNamespace(text="خبر"))
    normalized = ContentNormalizer().normalize(event.message)
    fingerprints = ContentPipeline._fingerprints(event, normalized, DeduplicationProfile())
    kinds = {kind for kind, _value in fingerprints}
    assert {"telegram_message", "text", "media", "combined"}.issubset(kinds)
    assert all(len(value) == 64 for _kind, value in fingerprints if _kind != "telegram_message")


def test_disabled_deduplication_returns_no_fingerprints():
    event = make_event(SimpleNamespace(text="خبر"))
    normalized = ContentNormalizer().normalize(event.message)
    profile = DeduplicationProfile(enabled=False)
    assert ContentPipeline._fingerprints(event, normalized, profile) == ()


def test_pipeline_decision_values_are_stable():
    assert PipelineDecision.ACCEPTED.value == "accepted"
    assert PipelineDecision.FILTERED.value == "filtered"
    assert PipelineDecision.DUPLICATE.value == "duplicate"
