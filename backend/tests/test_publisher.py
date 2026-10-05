from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime
from types import SimpleNamespace

from app.database import Database
from app.models import Source
from app.relay.publisher import Publisher
from app.repositories.events import EventLogRepository
from app.repositories.settings import SettingsRepository


class FakeTelegramClient:
    def __init__(self):
        self.sent_messages = []
        self.sent_files = []

    async def send_message(self, target, text):
        self.sent_messages.append((target, text))

    async def send_file(self, target, files, caption=None):
        self.sent_files.append((target, files, caption))


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


def test_publisher_sends_transformed_text_and_album(tmp_path):
    async def scenario():
        database = Database(tmp_path / "relay.sqlite3")
        await database.initialize()
        settings = SettingsRepository(database)
        await settings.update_target("@target", -1002)
        await settings.update_branding("حقوقنا", "https://t.me/ours")
        await settings.set_enabled(True)
        client = FakeTelegramClient()
        publisher = Publisher(client, settings, EventLogRepository(database))

        text_result = await publisher.publish(
            source(), [message(1, "خبر 🔥 https://source.example")]
        )
        assert text_result.published
        assert client.sent_messages == [(-1002, "خبر\n\nحقوقنا\n\nhttps://t.me/ours")]

        album_result = await publisher.publish(
            source(),
            [
                message(2, "ألبوم", media="photo-a", photo=object(), grouped_id=8),
                message(3, "", media="photo-b", photo=object(), grouped_id=8),
            ],
        )
        assert album_result.message_count == 2
        assert client.sent_files[0] == (
            -1002,
            ["photo-a", "photo-b"],
            "ألبوم\n\nحقوقنا\n\nhttps://t.me/ours",
        )

    asyncio.run(scenario())

    with sqlite3.connect(tmp_path / "relay.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM event_log").fetchone()[0] == 2
