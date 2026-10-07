"""Pure contracts for V3 route-specific content processing."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum

from app.db.models import ContentType


class ProcessingDecision(StrEnum):
    READY_FOR_DEDUP = "ready_for_dedup"
    FILTERED = "filtered"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class BrandingSpec:
    enabled: bool = False
    footer: str | None = None
    link: str | None = None
    separator: str | None = None


@dataclass(frozen=True, slots=True)
class RouteContentPolicy:
    include_keywords: tuple[str, ...] = ()
    exclude_keywords: tuple[str, ...] = ()
    allowed_media_types: frozenset[str] = frozenset()
    remove_telegram_urls: bool = True
    remove_general_urls: bool = True
    remove_source_rights: bool = True
    preserve_emoji: bool = True
    preserve_line_breaks: bool = True
    trim_whitespace: bool = True
    max_blank_lines: int = 1
    branding: BrandingSpec = BrandingSpec()


@dataclass(frozen=True, slots=True)
class NormalizedMedia:
    media_type: str
    identity: str
    source_message_id: int
    file_name: str | None = None
    mime_type: str | None = None
    byte_size: int | None = None


@dataclass(frozen=True, slots=True)
class ProcessedContent:
    normalized_text: str
    rendered_text: str
    content_type: ContentType
    media: tuple[NormalizedMedia, ...]


@dataclass(frozen=True, slots=True)
class ProcessingResult:
    execution_id: uuid.UUID
    decision: ProcessingDecision
    reason_code: str
    content: ProcessedContent | None = None
