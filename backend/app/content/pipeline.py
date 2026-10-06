"""Content processing pipeline for v2 ingestion events."""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    ContentFingerprint,
    ContentItem,
    ContentMedia,
    ContentStatus,
    ContentType,
    DeduplicationProfile,
    FilterProfile,
    RetentionMode,
    SourceRoute,
    TransformProfile,
)
from app.telegram.v2_listener import IngestionEvent


class PipelineDecision(StrEnum):
    ACCEPTED = "accepted"
    FILTERED = "filtered"
    DUPLICATE = "duplicate"


@dataclass(frozen=True, slots=True)
class ExtractedMedia:
    """Media metadata extracted without downloading the Telegram file."""

    media_type: str
    identity: str
    file_name: str | None = None
    mime_type: str | None = None
    byte_size: int | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class NormalizedContent:
    original_text: str
    normalized_text: str
    content_type: ContentType
    media: tuple[ExtractedMedia, ...]


@dataclass(frozen=True, slots=True)
class PipelineResult:
    decision: PipelineDecision
    reason: str
    event: IngestionEvent
    content_item_id: uuid.UUID | None = None


class ContentNormalizer:
    """Normalize text while preserving meaningful line breaks and emoji."""

    _telegram_url = re.compile(r"https?://t\.me/[A-Za-z0-9_+/?.=&%-]+", re.IGNORECASE)
    _separator = re.compile(r"^[\sــ_\-—–=•♦️|]{4,}$")

    def normalize(
        self,
        message: Any,
        *,
        remove_urls: bool = True,
        remove_source_rights: bool = True,
        preserve_line_breaks: bool = True,
        trim_whitespace: bool = True,
    ) -> NormalizedContent:
        original = self._extract_text(message)
        text = original.replace("\r\n", "\n").replace("\r", "\n")
        lines = text.split("\n")
        if remove_source_rights:
            lines = self._remove_rights_blocks(lines)
        if preserve_line_breaks:
            text = "\n".join(line.rstrip() for line in lines)
        else:
            text = " ".join(line.strip() for line in lines if line.strip())
        if remove_urls:
            text = self._telegram_url.sub("", text)
        if trim_whitespace:
            text = "\n".join(line.strip() for line in text.split("\n"))
            text = text.strip()
        media = tuple(self._extract_media(message))
        return NormalizedContent(
            original_text=original,
            normalized_text=text,
            content_type=self._content_type(media, text),
            media=media,
        )

    @staticmethod
    def _extract_text(message: Any) -> str:
        for name in ("raw_text", "message", "text", "caption"):
            value = getattr(message, name, None)
            if isinstance(value, str):
                return value
        return ""

    @classmethod
    def _remove_rights_blocks(cls, lines: list[str]) -> list[str]:
        result: list[str] = []
        index = 0
        while index < len(lines):
            line = lines[index]
            has_telegram_link = bool(cls._telegram_url.search(line))
            if has_telegram_link:
                index += 1
                while result and (
                    not result[-1].strip() or cls._separator.fullmatch(result[-1].strip())
                ):
                    result.pop()
                continue
            if cls._separator.fullmatch(line.strip()) and index + 1 < len(lines):
                next_line = lines[index + 1]
                if cls._telegram_url.search(next_line):
                    index += 2
                    continue
            result.append(line)
            index += 1
        return result

    @classmethod
    def _extract_media(cls, message: Any) -> list[ExtractedMedia]:
        media = getattr(message, "media", None)
        if media is None:
            return []
        candidates = media if isinstance(media, (list, tuple)) else [media]
        extracted = []
        for item in candidates:
            media_type = cls._media_type(item)
            identity = cls._media_identity(item)
            extracted.append(
                ExtractedMedia(
                    media_type=media_type,
                    identity=identity,
                    file_name=getattr(item, "file_name", None),
                    mime_type=getattr(item, "mime_type", None),
                    byte_size=getattr(item, "size", None),
                    metadata={"telegram_type": type(item).__name__},
                )
            )
        return extracted

    @staticmethod
    def _media_type(media: Any) -> str:
        declared = getattr(media, "media_type", None) or getattr(media, "type", None)
        if isinstance(declared, str):
            return declared.lower()
        name = type(media).__name__.lower()
        for candidate in ("photo", "video", "document", "audio", "voice"):
            if candidate in name:
                return candidate
        return "unknown"

    @staticmethod
    def _media_identity(media: Any) -> str:
        for name in ("file_unique_id", "id", "document_id", "access_hash"):
            value = getattr(media, name, None)
            if value is not None:
                return f"{name}:{value}"
        return f"object:{type(media).__name__}:{id(media)}"

    @staticmethod
    def _content_type(media: tuple[ExtractedMedia, ...], text: str) -> ContentType:
        if len(media) > 1:
            return ContentType.ALBUM
        if media:
            mapping = {
                "photo": ContentType.PHOTO,
                "video": ContentType.VIDEO,
                "document": ContentType.DOCUMENT,
                "audio": ContentType.AUDIO,
                "voice": ContentType.VOICE,
            }
            return mapping.get(media[0].media_type, ContentType.UNKNOWN)
        return ContentType.TEXT if text else ContentType.UNKNOWN


class FilterEngine:
    """Apply a route's deterministic inclusion/exclusion rules."""

    def apply(self, content: NormalizedContent, profile: FilterProfile | None) -> str | None:
        if profile is None:
            return None
        include = _string_list(profile.include_keywords)
        exclude = _string_list(profile.exclude_keywords)
        haystack = content.normalized_text.casefold()
        if include and not any(keyword.casefold() in haystack for keyword in include):
            return "missing_include_keyword"
        if any(keyword.casefold() in haystack for keyword in exclude):
            return "matched_exclude_keyword"
        allowed_media = set(_string_list(profile.allowed_media_types))
        if allowed_media and any(item.media_type not in allowed_media for item in content.media):
            return "media_type_not_allowed"
        if not content.normalized_text and not content.media:
            return "empty_content"
        return None


class ContentPipeline:
    """Transform, filter, deduplicate, and persist accepted ingestion events."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        account_id: uuid.UUID,
        *,
        normalizer: ContentNormalizer | None = None,
        filters: FilterEngine | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.account_id = account_id
        self.normalizer = normalizer or ContentNormalizer()
        self.filters = filters or FilterEngine()

    async def process(self, event: IngestionEvent) -> PipelineResult:
        async with self.session_factory() as session:
            async with session.begin():
                route_config = await self._load_route_config(session, event)
                content = self.normalizer.normalize(
                    event.message,
                    remove_urls=route_config[1].remove_urls if route_config[1] else True,
                    remove_source_rights=(
                        route_config[1].remove_source_rights if route_config[1] else True
                    ),
                    preserve_line_breaks=(
                        route_config[2].preserve_line_breaks if route_config[2] else True
                    ),
                    trim_whitespace=(route_config[2].trim_whitespace if route_config[2] else True),
                )
                filter_reason = self.filters.apply(content, route_config[1])
                if filter_reason:
                    return PipelineResult(PipelineDecision.FILTERED, filter_reason, event)

                fingerprints = self._fingerprints(event, content, route_config[3])
                if await self._is_duplicate(session, event.destination_id, fingerprints):
                    return PipelineResult(PipelineDecision.DUPLICATE, "matching_fingerprint", event)

                try:
                    async with session.begin_nested():
                        item = ContentItem(
                            account_id=self.account_id,
                            source_id=event.source_id,
                            telegram_message_id=event.message_id,
                            telegram_grouped_id=event.grouped_id,
                            content_type=content.content_type,
                            text_original=content.original_text,
                            text_normalized=content.normalized_text,
                            telegram_created_at=event.received_at,
                            retention_mode=RetentionMode.NONE,
                            status=ContentStatus.READY,
                            metadata_json={
                                "route_id": str(event.route_id),
                                "destination_id": str(event.destination_id),
                            },
                        )
                        session.add(item)
                        await session.flush()
                        for fingerprint_type, value in fingerprints:
                            session.add(
                                ContentFingerprint(
                                    content_item_id=item.id,
                                    scope_key=str(event.destination_id),
                                    fingerprint_type=fingerprint_type,
                                    fingerprint=value,
                                )
                            )
                        for media in content.media:
                            session.add(
                                ContentMedia(
                                    content_item_id=item.id,
                                    media_type=media.media_type,
                                    original_file_name=media.file_name,
                                    mime_type=media.mime_type,
                                    byte_size=media.byte_size,
                                    metadata_json=media.metadata or {},
                                )
                            )
                        await session.flush()
                except IntegrityError:
                    return PipelineResult(PipelineDecision.DUPLICATE, "matching_fingerprint", event)
                return PipelineResult(PipelineDecision.ACCEPTED, "accepted", event, item.id)

    async def _load_route_config(
        self, session: AsyncSession, event: IngestionEvent
    ) -> tuple[
        SourceRoute, FilterProfile | None, TransformProfile | None, DeduplicationProfile | None
    ]:
        statement = (
            select(SourceRoute, FilterProfile, TransformProfile, DeduplicationProfile)
            .outerjoin(FilterProfile, FilterProfile.id == SourceRoute.filter_profile_id)
            .outerjoin(TransformProfile, TransformProfile.id == SourceRoute.transform_profile_id)
            .outerjoin(
                DeduplicationProfile,
                DeduplicationProfile.id == SourceRoute.deduplication_profile_id,
            )
            .where(
                SourceRoute.id == event.route_id,
                SourceRoute.account_id == self.account_id,
                SourceRoute.source_id == event.source_id,
                SourceRoute.destination_id == event.destination_id,
            )
        )
        row = (await session.execute(statement)).first()
        if row is None:
            raise ValueError("ingestion route is not available in this account")
        return row

    async def _is_duplicate(
        self,
        session: AsyncSession,
        destination_id: uuid.UUID,
        fingerprints: tuple[tuple[str, str], ...],
    ) -> bool:
        if not fingerprints:
            return False
        values = [value for _kind, value in fingerprints]
        statement = select(ContentFingerprint.id).where(
            ContentFingerprint.scope_key == str(destination_id),
            ContentFingerprint.fingerprint.in_(values),
        )
        return await session.scalar(statement) is not None

    @staticmethod
    def _fingerprints(
        event: IngestionEvent,
        content: NormalizedContent,
        profile: DeduplicationProfile | None,
    ) -> tuple[tuple[str, str], ...]:
        if profile is not None and profile.enabled is False:
            return ()
        result: list[tuple[str, str]] = []
        if profile is None or profile.compare_telegram_id is not False:
            result.append(("telegram_message", f"{event.source_id}:{event.message_id}"))
        if profile is None or profile.compare_text is not False:
            result.append(("text", _sha256(content.normalized_text)))
        if profile is None or profile.compare_media is not False:
            media_value = "|".join(item.identity for item in content.media)
            result.append(("media", _sha256(media_value)))
        result.append(
            (
                "combined",
                _sha256(
                    content.normalized_text
                    + "\n"
                    + "|".join(item.identity for item in content.media)
                ),
            )
        )
        return tuple(result)


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
