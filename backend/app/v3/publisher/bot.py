"""python-telegram-bot adapter for V3 target publishing."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from contextlib import ExitStack
from typing import Any

from telegram import (
    Bot,
    InputMediaAudio,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
)

from .contracts import StagedMedia, album_family


class PythonTelegramBotAdapter:
    """Thin Bot API adapter with one process-local send interval."""

    def __init__(
        self,
        token: str,
        *,
        bot: Any | None = None,
        send_interval_seconds: float = 1.1,
    ) -> None:
        if not token.strip() and bot is None:
            raise ValueError("bot token is required")
        if send_interval_seconds < 0:
            raise ValueError("send_interval_seconds cannot be negative")
        self.bot = bot or Bot(token=token)
        self.send_interval_seconds = send_interval_seconds
        self._lock = asyncio.Lock()
        self._last_send_at = 0.0
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        initialize = getattr(self.bot, "initialize", None)
        if initialize is not None:
            await initialize()
        self._started = True

    async def stop(self) -> None:
        if not self._started:
            return
        shutdown = getattr(self.bot, "shutdown", None)
        if shutdown is not None:
            await shutdown()
        self._started = False

    async def send_text(self, chat_id: int, text: str) -> int:
        result = await self._call(
            self.bot.send_message,
            chat_id=chat_id,
            text=text,
            disable_web_page_preview=True,
        )
        return _message_id(result)

    async def send_media(
        self,
        chat_id: int,
        media: StagedMedia,
        *,
        caption: str | None = None,
    ) -> int:
        method_name, parameter = _single_media_method(media.media_type)
        method = getattr(self.bot, method_name, None)
        if method is None:
            raise RuntimeError(f"Telegram Bot API method unavailable: {method_name}")
        with media.path.open("rb") as handle:
            result = await self._call(
                method,
                chat_id=chat_id,
                caption=caption,
                **{parameter: handle},
            )
        return _message_id(result)

    async def send_album(
        self,
        chat_id: int,
        media: Sequence[StagedMedia],
        *,
        caption: str | None = None,
    ) -> tuple[int, ...]:
        album_family([item.media_type for item in media])
        with ExitStack() as stack:
            input_media = []
            for index, item in enumerate(media):
                handle = stack.enter_context(item.path.open("rb"))
                item_caption = caption if index == 0 else None
                input_media.append(_album_input(item.media_type, handle, item_caption))
            results = await self._call(
                self.bot.send_media_group,
                chat_id=chat_id,
                media=input_media,
            )
        values = tuple(_message_id(item) for item in results)
        if len(values) != len(media):
            raise RuntimeError("Telegram media group response count mismatch")
        return values

    async def _call(self, method: Any, **kwargs: Any) -> Any:
        async with self._lock:
            remaining = self.send_interval_seconds - (time.monotonic() - self._last_send_at)
            if remaining > 0:
                await asyncio.sleep(remaining)
            result = await method(**kwargs)
            self._last_send_at = time.monotonic()
            return result


def _message_id(result: Any) -> int:
    value = getattr(result, "message_id", None) or getattr(result, "id", None)
    if value is None:
        raise RuntimeError("Telegram response did not contain a message id")
    message_id = int(value)
    if message_id <= 0:
        raise RuntimeError("Telegram response contained an invalid message id")
    return message_id


def _single_media_method(media_type: str) -> tuple[str, str]:
    value = media_type.strip().lower()
    return {
        "photo": ("send_photo", "photo"),
        "video": ("send_video", "video"),
        "audio": ("send_audio", "audio"),
        "voice": ("send_voice", "voice"),
        "document": ("send_document", "document"),
        "unknown": ("send_document", "document"),
    }.get(value, ("send_document", "document"))


def _album_input(media_type: str, handle: Any, caption: str | None) -> Any:
    value = media_type.strip().lower()
    if value == "photo":
        return InputMediaPhoto(media=handle, caption=caption)
    if value == "video":
        return InputMediaVideo(media=handle, caption=caption)
    if value == "audio":
        return InputMediaAudio(media=handle, caption=caption)
    if value in {"document", "unknown"}:
        return InputMediaDocument(media=handle, caption=caption)
    raise ValueError(f"unsupported Telegram album media type: {value}")
