"""Content processing pipeline for v2 ingestion events."""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    BrandingProfile,
    ContentFingerprint,
    ContentItem,
    ContentMedia,
    ContentStatus,
    ContentType,
    DeduplicationProfile,
    Destination,
    FilterProfile,
    RetentionMode,
    RetentionPolicy,
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


@dataclass(frozen=True, slots=True)
class RouteConfig:
    route: SourceRoute
    destination: Destination
    filters: FilterProfile | None
    transform: TransformProfile | None
    deduplication: DeduplicationProfile | None
    branding: BrandingProfile | None
    retention: RetentionPolicy | None


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
        messages = getattr(message, "messages", None)
        if messages:
            for item in messages:
                for name in ("raw_text", "message", "text", "caption"):
                    value = getattr(item, name, None)
                    if isinstance(value, str) and value.strip():
                        return value
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
            if cls._telegram_url.search(line):
                index += 1
                while result and (
                    not result[-1].strip() or cls._separator.fullmatch(result[-1].strip())
                ):
                    result.pop()
                continue
            if cls._separator.fullmatch(line.strip()) and index + 1 < len(lines):
                if cls._telegram_url.search(lines[index + 1]):
                    index += 2
                    continue
            result.append(line)
            index += 1
        return result

    @classmethod
    def _extract_media(cls, message: Any) -> list[ExtractedMedia]:
        messages = getattr(message, "messages", None)
        if messages:
            candidates = [getattr(item, "media", None) for item in messages]
        else:
            media = getattr(message, "media", None)
            candidates = list(media) if isinstance(media, (list, tuple)) else [media]
        extracted: list[ExtractedMedia] = []
        for index, item in enumerate(candidate for candidate in candidates if candidate is not None):
            file_name = cls._media_attr(item, "file_name")
            mime_type = cls._media_attr(item, "mime_type")
            size = cls._media_attr(item, "size")
            extracted.append(
                ExtractedMedia(
                    media_type=cls._media_type(item),
                    identity=cls._media_identity(item),
                    file_name=str(file_name) if file_name else None,
                    mime_type=str(mime_type) if mime_type else None,
                    byte_size=int(size) if isinstance(size, int) else None,
                    metadata={
                        "telegram_type": type(item).__name__,
                        "bundle_index": index,
                    },
                )
            )
        return extracted

    @staticmethod
    def _nested_media(media: Any) -> Any:
        for name in ("photo", "document", "webpage"):
            nested = getattr(media, name, None)
            if nested is not None:
                return nested
        return media

    @classmethod
    def _media_attr(cls, media: Any, name: str) -> Any:
        direct = getattr(media, name, None)
        if direct is not None:
            return direct
        nested = cls._nested_media(media)
        direct = getattr(nested, name, None)
        if direct is not None:
            return direct
        file_obj = getattr(nested, "file", None)
        return getattr(file_obj, name, None) if file_obj is not None else None

    @classmethod
    def _media_type(cls, media: Any) -> str:
        declared = getattr(media, "media_type", None) or getattr(media, "type", None)
        if isinstance(declared, str):
            return declared.lower()
        names = f"{type(media).__name__} {type(cls._nested_media(media)).__name__}".lower()
        if "photo" in names:
            return "photo"
        if "video" in names:
            return "video"
        if "audio" in names:
            return "audio"
        if "voice" in names:
            return "voice"
        if "document" in names:
            mime = str(cls._media_attr(media, "mime_type") or "").lower()
            if mime.startswith("video/"):
                return "video"
            if mime.startswith("audio/"):
                return "audio"
            return "document"
        return "unknown"

    @classmethod
    def _media_identity(cls, media: Any) -> str:
        nested = cls._nested_media(media)
        for candidate in (media, nested):
            for name in ("file_unique_id", "id", "document_id"):
                value = getattr(candidate, name, None)
                if value is not None:
                    return f"{name}:{value}"
        access_hash = getattr(nested, "access_hash", None)
        if access_hash is not None:
            return f"access_hash:{access_hash}"
        return f"type:{type(media).__name__}:{repr(media)[:160]}"

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
        include = _string_list(profile.include_keywords) if profile else []
        exclude = _string_list(profile.exclude_keywords) if profile else []
        haystack = content.normalized_text.casefold()
        if include and not any(keyword.casefold() in haystack for keyword in include):
            return "missing_include_keyword"
        if any(keyword.casefold() in haystack for keyword in exclude):
            return "matched_exclude_keyword"
        allowed_media = set(_string_list(profile.allowed_media_types)) if profile else set()
        if allowed_media and any(item.media_type not in allowed_media for item in content.media):
            return "media_type_not_allowed"
        if not content.normalized_text and not content.media:
            return "empty_content"
        return None


class ContentPipeline:
    """Transform, filter, deduplicate, brand, and persist ingestion events."""

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
                config = await self._load_route_config(session, event)
                content = self.normalizer.normalize(
                    event.message,
                    remove_urls=config.filters.remove_urls if config.filters else True,
                    remove_source_rights=(
                        config.filters.remove_source_rights if config.filters else True
                    ),
                    preserve_line_breaks=(
                        config.transform.preserve_line_breaks if config.transform else True
                    ),
                    trim_whitespace=(config.transform.trim_whitespace if config.transform else True),
                )
                filter_reason = self.filters.apply(content, config.filters)
                if filter_reason:
                    return PipelineResult(PipelineDecision.FILTERED, filter_reason, event)

                fingerprints = self._fingerprints(event, content, config.deduplication)
                if await self._is_duplicate(
                    session,
                    event.destination_id,
                    fingerprints,
                    config.deduplication,
                ):
                    return PipelineResult(PipelineDecision.DUPLICATE, "matching_fingerprint", event)

                branded_text = self._apply_branding(content.normalized_text, config.branding)
                retention_mode = (
                    config.retention.mode if config.retention is not None else RetentionMode.NONE
                )
                try:
                    async with session.begin_nested():
                        item = ContentItem(
                            account_id=self.account_id,
                            source_id=event.source_id,
                            telegram_message_id=event.message_id,
                            telegram_grouped_id=event.grouped_id,
                            content_type=content.content_type,
                            text_original=content.original_text,
                            text_normalized=branded_text,
                            telegram_created_at=event.received_at,
                            retention_mode=retention_mode,
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
                        for index, media in enumerate(content.media):
                            metadata = dict(media.metadata or {})
                            metadata.update(
                                {
                                    "telegram_chat_id": event.chat_id,
                                    "telegram_message_id": event.message_id,
                                    "telegram_grouped_id": event.grouped_id,
                                    "media_index": index,
                                }
                            )
                            session.add(
                                ContentMedia(
                                    content_item_id=item.id,
                                    media_type=media.media_type,
                                    original_file_name=media.file_name,
                                    mime_type=media.mime_type,
                                    byte_size=media.byte_size,
                                    metadata_json=metadata,
                                )
                            )
                        await session.flush()
                except IntegrityError:
                    return PipelineResult(PipelineDecision.DUPLICATE, "message_already_ingested", event)
                return PipelineResult(PipelineDecision.ACCEPTED, "accepted", event, item.id)

    async def _load_route_config(self, session: AsyncSession, event: IngestionEvent) -> RouteConfig:
        route = await session.scalar(
            select(SourceRoute).where(
                SourceRoute.id == event.route_id,
                SourceRoute.account_id == self.account_id,
                SourceRoute.source_id == event.source_id,
                SourceRoute.destination_id == event.destination_id,
            )
        )
        if route is None:
            raise ValueError("ingestion route is not available in this account")
        destination = await session.scalar(
            select(Destination).where(
                Destination.id == event.destination_id,
                Destination.account_id == self.account_id,
            )
        )
        if destination is None:
            raise ValueError("ingestion destination is not available in this account")

        async def profile(model: type, profile_id: uuid.UUID | None) -> Any:
            if profile_id is None:
                return None
            return await session.scalar(
                select(model).where(model.id == profile_id, model.account_id == self.account_id)
            )

        return RouteConfig(
            route=route,
            destination=destination,
            filters=await profile(FilterProfile, route.filter_profile_id),
            transform=await profile(TransformProfile, route.transform_profile_id),
            deduplication=await profile(
                DeduplicationProfile,
                route.deduplication_profile_id or destination.deduplication_profile_id,
            ),
            branding=await profile(
                BrandingProfile,
                route.branding_profile_id or destination.branding_profile_id,
            ),
            retention=await profile(
                RetentionPolicy,
                route.retention_policy_id or destination.retention_policy_id,
            ),
        )

    async def _is_duplicate(
        self,
        session: AsyncSession,
        destination_id: uuid.UUID,
        fingerprints: tuple[tuple[str, str], ...],
        profile: DeduplicationProfile | None,
    ) -> bool:
        if not fingerprints or (profile is not None and profile.enabled is False):
            return False

        match_types = {"telegram_message", "combined"}
        options = profile.options if profile is not None and isinstance(profile.options, dict) else {}
        if options.get("match_text_only"):
            match_types.add("text")
        if options.get("match_media_only"):
            match_types.add("media")
        comparable = [(kind, value) for kind, value in fingerprints if kind in match_types]
        if not comparable:
            return False

        pairs = [
            and_(
                ContentFingerprint.fingerprint_type == kind,
                ContentFingerprint.fingerprint == value,
            )
            for kind, value in comparable
        ]
        statement = (
            select(ContentFingerprint.id)
            .join(ContentItem, ContentItem.id == ContentFingerprint.content_item_id)
            .where(
                ContentFingerprint.scope_key == str(destination_id),
                or_(*pairs),
            )
            .limit(1)
        )
        if profile is not None and profile.window_seconds > 0:
            cutoff = datetime.now(UTC) - timedelta(seconds=profile.window_seconds)
            statement = statement.where(ContentItem.created_at >= cutoff)
        return await session.scalar(statement) is not None

    @staticmethod
    def _fingerprints(
        event: IngestionEvent,
        content: NormalizedContent,
        profile: DeduplicationProfile | None,
    ) -> tuple[tuple[str, str], ...]:
        if profile is not None and profile.enabled is False:
            return ()

        compare_telegram = profile is None or profile.compare_telegram_id is not False
        compare_text = profile is None or profile.compare_text is not False
        compare_media = profile is None or profile.compare_media is not False
        result: list[tuple[str, str]] = []

        if compare_telegram:
            result.append(
                ("telegram_message", _sha256(f"{event.source_id}:{event.message_id}"))
            )

        components: list[str] = []
        if compare_text and content.normalized_text:
            text_hash = _sha256(content.normalized_text)
            result.append(("text", text_hash))
            components.append(f"text:{text_hash}")

        if compare_media and content.media:
            media_hash = _sha256("|".join(item.identity for item in content.media))
            result.append(("media", media_hash))
            components.append(f"media:{media_hash}")

        if components:
            result.append(("combined", _sha256("\n".join(components))))
        return tuple(result)

    @staticmethod
    def _apply_branding(text: str, profile: BrandingProfile | None) -> str:
        if profile is None or profile.enabled is False:
            return text
        blocks = [text.strip()] if text.strip() else []
        footer = (profile.footer or "").strip()
        link = (profile.link or "").strip()
        if footer or link:
            separator = (profile.separator or "").strip()
            if separator and blocks:
                blocks.append(separator)
            if footer:
                blocks.append(footer)
            if link:
                blocks.append(link)
        return "\n".join(blocks).strip()


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
