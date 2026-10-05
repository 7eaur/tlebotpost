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


def test_transformer_removes_trailing_source_signature_block():
    result = ContentTransformer(settings()).transform(
        "خبر عاجل 🔥\n"
        "♦️\n"
        "ــــــــــــــــــــــــــــــــــــــــــــــ\n"
        "🇾🇪 اخبار اليمن العاجلة 🇾🇪https://t.me/newsyemn"
    )

    assert result.text == "خبر عاجل\n\nحقوقنا\n\nhttps://t.me/ours"
    assert "newsyemn" not in result.text
    assert "اخبار اليمن" not in result.text
    assert "ــــــــ" not in result.text


def test_transformer_removes_telegram_links_without_dropping_regular_text():
    result = ContentTransformer(settings()).transform(
        "تابع التفاصيل هنا https://t.me/source_channel وشاركها"
    )

    assert result.text == "تابع التفاصيل هنا وشاركها\n\nحقوقنا\n\nhttps://t.me/ours"


def test_transformer_does_not_duplicate_our_configured_signature():
    result = ContentTransformer(settings()).transform(
        "خبر جديد\nحقوقنا\nhttps://t.me/ours"
    )

    assert result.text == "خبر جديد\n\nحقوقنا\n\nhttps://t.me/ours"


def test_transformer_removes_repeated_plain_source_stamp():
    result = ContentTransformer(settings()).transform(
        "خبر عاجل\n"
        "ــــــــــــــــــــــــــــــــــــــــــــــ\n"
        "اخبار اليمن العاجلة\n"
        "tlebotpost\n"
        "tlebotpost\n"
        "tlebotpost\n"
        "tlebotpost"
    )

    assert result.text == "خبر عاجل\n\nحقوقنا\n\nhttps://t.me/ours"
    assert "tlebotpost" not in result.text


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
