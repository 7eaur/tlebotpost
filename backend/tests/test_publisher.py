from __future__ import annotations

import asyncio
import os
import sqlite3
from datetime import UTC, datetime
from types import SimpleNamespace

from app.database import Database
from app.models import Source
from app.relay.publisher import Publisher
from app.repositories.events import EventLogRepository
from app.repositories.settings import SettingsRepository


class FakeReaderClient:
    def __init__(self):
        self.paths = []

    async def download_media(self, message, file):
        self.paths.append(file)
        with open(file, "wb") as handle:
            handle.write(f"media-{message.id}".encode())
        return file


class FakeBot:
    def __init__(self):
        self.sent_messages = []
        self.sent_photos = []
        self.sent_groups = []

    async def send_message(self, target, text):
        self.sent_messages.append((target, text))

    async def send_photo(self, target, photo, caption=None):
        self.sent_photos.append((target, photo.read(), caption))

    async def send_media_group(self, target, media):
        self.sent_groups.append(
            (
                target,
                [(_read_input_file(item.media), item.caption) for item in media],
            )
        )


def _read_input_file(value):
    if hasattr(value, "input_file_content"):
        return value.input_file_content
    return value.read()


def source() -> Source:
    now = datetime.now(UTC)
    return Source(1, -1001, "@source", "Source", "source", True, 0, now, now)


def message(message_id, text="", *, media=None, photo=None, grouped_id=None):
    return SimpleNamespace(
        id=message_id,
        message=text,
        media=media,
        photo=photo,
        grouped_id=grouped_id,
    )


def test_publisher_uses_bot_api_for_text_and_media(tmp_path):
    async def scenario():
        database = Database(tmp_path / "relay.sqlite3")
        await database.initialize()
        settings = SettingsRepository(database)
        await settings.update_target("@target", -1002)
        await settings.update_branding("حقوقنا", "https://t.me/ours")
        await settings.set_enabled(True)
        reader = FakeReaderClient()
        bot = FakeBot()
        publisher = Publisher(
            reader, bot, settings, EventLogRepository(database), send_interval_seconds=0
        )

        text_result = await publisher.publish(
            source(), [message(1, "خبر 🔥 https://source.example")]
        )
        assert text_result.published
        assert bot.sent_messages == [(-1002, "خبر 🔥\n\nحقوقنا\nhttps://t.me/ours")]

        media_result = await publisher.publish(
            source(), [message(2, "صورة", media="photo-a", photo=object())]
        )
        assert media_result.published
        assert bot.sent_photos[0][0] == -1002
        assert bot.sent_photos[0][1] == b"media-2"
        assert bot.sent_photos[0][2] == "صورة\n\nحقوقنا\nhttps://t.me/ours"

        album_result = await publisher.publish(
            source(),
            [
                message(3, "ألبوم", media="photo-a", photo=object(), grouped_id=8),
                message(4, "", media="photo-b", photo=object(), grouped_id=8),
            ],
        )
        assert album_result.message_count == 2
        assert bot.sent_groups == [
            (
                -1002,
                [
                    (b"media-3", "ألبوم\n\nحقوقنا\nhttps://t.me/ours"),
                    (b"media-4", None),
                ],
            )
        ]
        assert all(not os.path.exists(path) for path in reader.paths)

    asyncio.run(scenario())

    with sqlite3.connect(tmp_path / "relay.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM event_log").fetchone()[0] == 3


def test_publisher_does_not_publish_webpage_preview_as_media(tmp_path):
    async def scenario():
        database = Database(tmp_path / "relay.sqlite3")
        await database.initialize()
        settings = SettingsRepository(database)
        await settings.update_target("@target", -1002)
        await settings.update_branding("حقوقنا", "https://t.me/ours")
        await settings.set_enabled(True)
        reader = FakeReaderClient()
        bot = FakeBot()
        publisher = Publisher(
            reader, bot, settings, EventLogRepository(database), send_interval_seconds=0
        )
        webpage_message = message(
            9,
            "شاهد الرابط https://t.me/source",
            media=SimpleNamespace(webpage=object()),
        )

        result = await publisher.publish(source(), [webpage_message])

        assert result.published
        assert bot.sent_messages == [
            (-1002, "شاهد الرابط\n\nحقوقنا\nhttps://t.me/ours")
        ]
        assert reader.paths == []

    asyncio.run(scenario())
