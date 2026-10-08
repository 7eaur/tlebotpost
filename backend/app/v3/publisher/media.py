"""Transient source-media staging for V3 publishing attempts."""

from __future__ import annotations

import shutil
import time
import uuid
from pathlib import Path
from typing import Sequence

from telethon.errors import FloodWaitError

from app.v3.content import NormalizedMedia
from app.v3.telegram import (
    TelegramMediaUnavailable,
    TelegramMessageUnavailable,
    TelegramUserAdapter,
)

from .contracts import StagedMedia


class MediaAcquisitionError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds


class MediaStagerV3:
    """Download exact accepted source messages into attempt-scoped temporary directories."""

    def __init__(
        self,
        adapter: TelegramUserAdapter,
        root: str | Path,
        *,
        stale_after_seconds: int = 21600,
    ) -> None:
        if stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be positive")
        self.adapter = adapter
        self.root = Path(root)
        self.stale_after_seconds = stale_after_seconds

    async def stage(
        self,
        *,
        job_id: uuid.UUID,
        attempt_count: int,
        chat_id: int,
        media: Sequence[NormalizedMedia],
    ) -> tuple[StagedMedia, ...]:
        if attempt_count <= 0:
            raise ValueError("attempt_count must be positive")
        job_dir = self._job_dir(job_id, attempt_count)
        self._remove(job_dir)
        job_dir.mkdir(parents=True, exist_ok=True)

        staged: list[StagedMedia] = []
        try:
            for index, item in enumerate(media):
                item_dir = job_dir / f"{index:02d}-{item.source_message_id}"
                item_dir.mkdir(parents=True, exist_ok=True)
                path = await self.adapter.download_message_media(
                    chat_id,
                    item.source_message_id,
                    item_dir,
                )
                staged.append(
                    StagedMedia(
                        media_type=item.media_type,
                        path=Path(path),
                        source_message_id=item.source_message_id,
                        file_name=item.file_name,
                        mime_type=item.mime_type,
                    )
                )
        except (TelegramMessageUnavailable, TelegramMediaUnavailable) as exc:
            self._remove(job_dir)
            raise MediaAcquisitionError(
                "source_media_unavailable",
                str(exc),
                retryable=False,
            ) from exc
        except FloodWaitError as exc:
            self._remove(job_dir)
            raise MediaAcquisitionError(
                "source_media_flood_wait",
                "Telegram user session requested a media-download wait",
                retryable=True,
                retry_after_seconds=max(1, int(exc.seconds)),
            ) from exc
        except (TimeoutError, ConnectionError, OSError) as exc:
            self._remove(job_dir)
            raise MediaAcquisitionError(
                "source_media_temporary_error",
                type(exc).__name__,
                retryable=True,
            ) from exc
        except Exception as exc:
            self._remove(job_dir)
            raise MediaAcquisitionError(
                "source_media_download_error",
                type(exc).__name__,
                retryable=True,
            ) from exc

        if len(staged) != len(media):
            self._remove(job_dir)
            raise MediaAcquisitionError(
                "source_media_count_mismatch",
                "not all source media items were staged",
                retryable=False,
            )
        return tuple(staged)

    def cleanup(self, *, job_id: uuid.UUID, attempt_count: int) -> None:
        self._remove(self._job_dir(job_id, attempt_count))

    def cleanup_stale(self, *, now: float | None = None) -> int:
        if not self.root.exists():
            return 0
        current = time.time() if now is None else now
        removed = 0
        for child in self.root.iterdir():
            if not child.is_dir() or not child.name.startswith("job-"):
                continue
            try:
                age = current - child.stat().st_mtime
            except FileNotFoundError:
                continue
            if age < self.stale_after_seconds:
                continue
            self._remove(child)
            removed += 1
        return removed

    def _job_dir(self, job_id: uuid.UUID, attempt_count: int) -> Path:
        return self.root / f"job-{job_id}-attempt-{attempt_count}"

    @staticmethod
    def _remove(path: Path) -> None:
        shutil.rmtree(path, ignore_errors=True)
