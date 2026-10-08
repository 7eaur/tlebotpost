from __future__ import annotations

from pathlib import Path

import pytest

from app.v3.publisher import (
    TELEGRAM_CAPTION_LIMIT,
    TELEGRAM_TEXT_LIMIT,
    MediaStagerV3,
    album_family,
    split_telegram_text,
)


def test_split_telegram_text_preserves_all_words_without_truncation():
    text = ("سطر عربي طويل " * 700).strip()
    chunks = split_telegram_text(text)

    assert len(chunks) > 1
    assert all(0 < len(chunk) <= TELEGRAM_TEXT_LIMIT for chunk in chunks)
    assert " ".join(" ".join(chunks).split()) == " ".join(text.split())


def test_split_telegram_text_prefers_line_boundary():
    text = ("أ" * 3000) + "\n" + ("ب" * 2000)
    chunks = split_telegram_text(text)

    assert chunks[0] == "أ" * 3000
    assert chunks[1] == "ب" * 2000


def test_caption_limit_is_stricter_than_text_limit():
    assert TELEGRAM_CAPTION_LIMIT == 1024
    assert TELEGRAM_TEXT_LIMIT == 4096


def test_album_family_accepts_telegram_supported_groups():
    assert album_family(["photo", "video"]) == "visual"
    assert album_family(["document", "document"]) == "document"
    assert album_family(["audio", "audio"]) == "audio"


@pytest.mark.parametrize(
    "values",
    [
        ["photo"],
        ["voice", "voice"],
        ["photo", "document"],
        ["audio", "document"],
        ["photo"] * 11,
    ],
)
def test_album_family_rejects_unrepresentable_groups(values):
    with pytest.raises(ValueError):
        album_family(values)


def test_media_stager_removes_only_stale_managed_directories(tmp_path: Path):
    class Adapter:
        pass

    stager = MediaStagerV3(Adapter(), tmp_path, stale_after_seconds=10)
    stale = tmp_path / "job-old-attempt-1"
    fresh = tmp_path / "job-new-attempt-1"
    unrelated = tmp_path / "keep-me"
    stale.mkdir()
    fresh.mkdir()
    unrelated.mkdir()

    stale_time = 100.0
    fresh_time = 195.0
    stale.touch()
    fresh.touch()
    import os

    os.utime(stale, (stale_time, stale_time))
    os.utime(fresh, (fresh_time, fresh_time))

    assert stager.cleanup_stale(now=200.0) == 1
    assert not stale.exists()
    assert fresh.exists()
    assert unrelated.exists()
