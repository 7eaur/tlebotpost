from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

from app.db.models import ContentType
from app.v3.content import (
    BrandingRendererV3,
    BrandingSpec,
    ContentFilterV3,
    ContentNormalizerV3,
    ProcessingDecision,
    RouteContentPolicy,
)
from app.v3.telegram.types import SourceEvent


def message(
    message_id: int,
    *,
    text: str = "",
    grouped_id: int | None = None,
    media=None,
):
    return SimpleNamespace(
        id=message_id,
        grouped_id=grouped_id,
        date=datetime(2026, 10, 8, 0, 0, message_id % 60, tzinfo=UTC),
        raw_text=text,
        media=media,
    )


def source_event(*messages):
    return SourceEvent.from_messages(
        account_id=uuid.uuid4(),
        source_id=uuid.uuid4(),
        chat_id=-1001,
        messages=tuple(messages),
    )


def test_arabic_normalization_removes_urls_and_trailing_source_rights_but_preserves_emoji():
    event = source_event(
        message(
            1,
            text=(
                "  خبر مهم 😀  \r\n\r\n"
                "التفاصيل https://example.com/story \r\n"
                "ــــــــــــ\r\n"
                "المصدر: https://t.me/source_channel"
            ),
        )
    )

    content = ContentNormalizerV3().normalize(event, RouteContentPolicy())

    assert content.normalized_text == "خبر مهم 😀\n\nالتفاصيل"
    assert content.rendered_text == content.normalized_text
    assert content.content_type is ContentType.TEXT
    assert content.media == ()


def test_telegram_and_general_url_rules_are_independent():
    event = source_event(
        message(2, text="مرجع https://example.com/page وقناة https://t.me/channel")
    )
    policy = RouteContentPolicy(
        remove_telegram_urls=True,
        remove_general_urls=False,
        remove_source_rights=False,
    )

    content = ContentNormalizerV3().normalize(event, policy)

    assert "https://example.com/page" in content.normalized_text
    assert "t.me/channel" not in content.normalized_text


def test_emoji_can_be_removed_without_damaging_arabic_text():
    event = source_event(message(3, text="العربية ممتازة ✅🔥 123"))
    policy = RouteContentPolicy(preserve_emoji=False)

    content = ContentNormalizerV3().normalize(event, policy)

    assert content.normalized_text == "العربية ممتازة  123"
    assert "العربية" in content.normalized_text


def test_line_break_policy_can_flatten_and_trim_text():
    event = source_event(message(4, text="  سطر أول \n\n سطر ثان  "))
    policy = RouteContentPolicy(preserve_line_breaks=False, trim_whitespace=True)

    content = ContentNormalizerV3().normalize(event, policy)

    assert content.normalized_text == "سطر أول سطر ثان"


def test_album_is_one_logical_content_unit_with_ordered_media_and_caption():
    first_media = SimpleNamespace(media_type="photo", id=101, mime_type="image/jpeg", size=10)
    second_media = SimpleNamespace(media_type="video", id=202, mime_type="video/mp4", size=20)
    event = source_event(
        message(12, text="عنوان الألبوم", grouped_id=77, media=second_media),
        message(11, text="عنوان الألبوم", grouped_id=77, media=first_media),
    )

    content = ContentNormalizerV3().normalize(event, RouteContentPolicy())

    assert content.content_type is ContentType.ALBUM
    assert content.normalized_text == "عنوان الألبوم"
    assert [item.media_type for item in content.media] == ["photo", "video"]
    assert [item.identity for item in content.media] == ["id:101", "id:202"]
    assert [item.source_message_id for item in content.media] == [11, 12]


def test_filter_reasons_are_deterministic():
    normalizer = ContentNormalizerV3()
    filters = ContentFilterV3()
    text = normalizer.normalize(source_event(message(5, text="خبر عادي")), RouteContentPolicy())

    assert (
        filters.reason(text, RouteContentPolicy(include_keywords=("عاجل",)))
        == "missing_include_keyword"
    )
    assert (
        filters.reason(text, RouteContentPolicy(exclude_keywords=("عادي",)))
        == "matched_exclude_keyword"
    )

    media = normalizer.normalize(
        source_event(message(6, media=SimpleNamespace(media_type="video", id=303))),
        RouteContentPolicy(),
    )
    assert (
        filters.reason(media, RouteContentPolicy(allowed_media_types=frozenset({"photo"})))
        == "media_type_not_allowed"
    )

    empty = normalizer.normalize(source_event(message(7)), RouteContentPolicy())
    assert filters.reason(empty, RouteContentPolicy()) == "empty_content"


def test_branding_is_rendered_after_normalization_without_changing_dedup_text():
    content = ContentNormalizerV3().normalize(
        source_event(message(8, text="خبر")),
        RouteContentPolicy(),
    )
    policy = RouteContentPolicy(
        branding=BrandingSpec(
            enabled=True,
            separator="———",
            footer="وصل الأخبار",
            link="https://example.com",
        )
    )

    rendered = BrandingRendererV3().render(content, policy)

    assert rendered.normalized_text == "خبر"
    assert rendered.rendered_text == "خبر\n———\nوصل الأخبار\nhttps://example.com"


def test_processing_decision_contract_exposes_phase_boundary():
    assert ProcessingDecision.READY_FOR_DEDUP.value == "ready_for_dedup"
    assert ProcessingDecision.FILTERED.value == "filtered"
