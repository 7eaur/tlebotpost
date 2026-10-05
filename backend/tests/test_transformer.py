from __future__ import annotations

from app.models import RelaySettings
from app.relay.transformer import ContentTransformer


def settings(**overrides) -> RelaySettings:
    values = dict(
        target_ref="@target",
        target_chat_id=-1002,
        brand_footer="حقوقنا",
        brand_link="https://t.me/ours",
        enabled=True,
        include_keywords=(),
        exclude_keywords=(),
        allowed_media_types=(),
    )
    values.update(overrides)
    return RelaySettings(**values)


def test_transformer_removes_urls_rights_and_emoji():
    result = ContentTransformer(settings()).transform(
        "خبر مهم 🔥\nالمصدر: https://source.example\nتابع التفاصيل https://example.com/page"
    )

    assert result.should_publish
    assert result.text == "خبر مهم\nتابع التفاصيل\n\nحقوقنا\n\nhttps://t.me/ours"
    assert "🔥" not in result.text
    assert "source.example" not in result.text


def test_transformer_applies_include_exclude_and_media_filters():
    transformer = ContentTransformer(
        settings(
            include_keywords=("خبر",),
            exclude_keywords=("إعلان",),
            allowed_media_types=("photo",),
        )
    )
    assert transformer.transform("خبر جديد", media_types=("photo",)).should_publish
    assert (
        transformer.transform("خبر جديد", media_types=("video",)).skipped_reason
        == "disallowed_media_type"
    )
    assert (
        transformer.transform("نص بلا الكلمة", media_types=("photo",)).skipped_reason
        == "missing_included_keyword"
    )
    assert (
        transformer.transform("خبر وإعلان", media_types=("photo",)).skipped_reason
        == "excluded_keyword"
    )


def test_transformer_skips_empty_text_without_media():
    result = ContentTransformer(settings()).transform("🔥 https://example.com")
    assert result.should_publish is False
    assert result.skipped_reason == "empty_after_cleaning"
