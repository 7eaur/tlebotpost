from __future__ import annotations

import asyncio

import pytest

from app.database import Database
from app.repositories.settings import SettingsRepository


def test_settings_repository_persists_target_branding_and_filters(tmp_path):
    async def scenario():
        database = Database(tmp_path / "relay.sqlite3")
        await database.initialize()
        repository = SettingsRepository(database)

        initial = await repository.get()
        assert initial.target_ref is None
        assert initial.enabled is False

        await repository.update_target("@target", -1002)
        await repository.update_branding("حقوقنا", "https://t.me/ours")
        await repository.update_filters(
            include_keywords=("خبر", "خبر", " تقنية "),
            exclude_keywords=("إعلان",),
            allowed_media_types=("photo", "video"),
        )
        updated = await repository.set_enabled(True)

        assert updated.target_ref == "@target"
        assert updated.target_chat_id == -1002
        assert updated.brand_footer == "حقوقنا"
        assert updated.brand_link == "https://t.me/ours"
        assert updated.include_keywords == ("خبر", "تقنية")
        assert updated.exclude_keywords == ("إعلان",)
        assert updated.allowed_media_types == ("photo", "video")
        assert updated.enabled is True

    asyncio.run(scenario())


def test_settings_repository_requires_branding(tmp_path):
    async def scenario():
        database = Database(tmp_path / "relay.sqlite3")
        await database.initialize()
        repository = SettingsRepository(database)
        with pytest.raises(ValueError, match="footer or link"):
            await repository.update_branding(" ", " ")

    asyncio.run(scenario())
