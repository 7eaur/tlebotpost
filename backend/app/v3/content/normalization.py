"""Deterministic text/media normalization for V3 content processing."""

from __future__ import annotations

import re
from typing import Any

from app.db.models import ContentType
from app.v3.telegram.types import SourceEvent

from .contracts import NormalizedMedia, ProcessedContent, RouteContentPolicy

_TELEGRAM_URL_RE = re.compile(
    r"(?i)(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me|telegram\.dog)/[^\s<>()]+"
)
_GENERAL_URL_RE = re.compile(r"(?i)(?:https?://|www\.)[^\s<>()]+")
_SEPARATOR_RE = re.compile(r"^[\sـ_\-—–=•♦|·.]{4,}$")
_RIGHTS_MARKERS = (
    "حقوق",
    "المصدر",
    "مصدر:",
    "المصدر:",
    "source",
    "credit",
)

# Ranges deliberately target emoji/symbol blocks rather than Arabic/punctuation broadly.
_EMOJI_RANGES = (
    (0x1F1E6, 0x1F1FF),
    (0x1F300, 0x1FAFF),
    (0x2600, 0x27BF),
    (0x2300, 0x23FF),
)
_EMOJI_JOINERS = {0x200D, 0xFE0E, 0xFE0F, 0x20E3}


class ContentNormalizerV3:
    """Normalize a logical SourceEvent without deduplication or persistence."""

    def normalize(self, event: SourceEvent, policy: RouteContentPolicy) -> ProcessedContent:
        original = self._event_text(event)
        text = original.replace("\r\n", "\n").replace("\r", "\n")

        if policy.remove_source_rights:
            text = self._remove_trailing_source_rights(text)
        if policy.remove_telegram_urls:
            text = _TELEGRAM_URL_RE.sub("", text)
        if policy.remove_general_urls:
            text = _GENERAL_URL_RE.sub("", text)
        if not policy.preserve_emoji:
            text = self._remove_emoji(text)

        text = self._normalize_whitespace(
            text,
            preserve_line_breaks=policy.preserve_line_breaks,
            trim_whitespace=policy.trim_whitespace,
            max_blank_lines=policy.max_blank_lines,
        )
        media = self._event_media(event)
        content_type = self._content_type(event, text, media)
        return ProcessedContent(
            normalized_text=text,
            rendered_text=text,
            content_type=content_type,
            media=media,
        )

    @staticmethod
    def _message_text(message: Any) -> str:
        for name in ("raw_text", "message", "text", "caption"):
            value = getattr(message, name, None)
            if isinstance(value, str) and value:
                return value
        return ""

    @classmethod
    def _event_text(cls, event: SourceEvent) -> str:
        parts: list[str] = []
        for message in event.messages:
            value = cls._message_text(message)
            if value and (not parts or value != parts[-1]):
                parts.append(value)
        return "\n".join(parts)

    @classmethod
    def _remove_trailing_source_rights(cls, text: str) -> str:
        lines = text.split("\n")
        if not lines:
            return text

        index = len(lines) - 1
        while index >= 0 and not lines[index].strip():
            index -= 1
        end = index
        found_rights = False

        while index >= 0:
            stripped = lines[index].strip()
            folded = stripped.casefold()
            is_separator = bool(_SEPARATOR_RE.fullmatch(stripped))
            is_rights = any(marker in folded for marker in _RIGHTS_MARKERS)
            is_telegram_credit = bool(_TELEGRAM_URL_RE.search(stripped))

            if is_rights or is_telegram_credit:
                found_rights = True
                index -= 1
                continue
            if found_rights and (not stripped or is_separator):
                index -= 1
                continue
            break

        if not found_rights:
            return text

        # Only trim a suffix; content above the identified rights block is untouched.
        kept = lines[: index + 1]
        while kept and not kept[-1].strip():
            kept.pop()
        if end < 0:
            return ""
        return "\n".join(kept)

    @staticmethod
    def _remove_emoji(text: str) -> str:
        chars: list[str] = []
        for char in text:
            codepoint = ord(char)
            if codepoint in _EMOJI_JOINERS:
                continue
            if any(start <= codepoint <= end for start, end in _EMOJI_RANGES):
                continue
            chars.append(char)
        return "".join(chars)

    @staticmethod
    def _normalize_whitespace(
        text: str,
        *,
        preserve_line_breaks: bool,
        trim_whitespace: bool,
        max_blank_lines: int,
    ) -> str:
        lines = text.split("\n")
        if trim_whitespace:
            lines = [line.strip() for line in lines]

        if not preserve_line_breaks:
            return " ".join(line for line in lines if line).strip()

        max_blank_lines = max(0, min(max_blank_lines, 3))
        normalized: list[str] = []
        blank_run = 0
        for line in lines:
            if line:
                normalized.append(line)
                blank_run = 0
                continue
            blank_run += 1
            if blank_run <= max_blank_lines:
                normalized.append("")

        return "\n".join(normalized).strip()

    @classmethod
    def _event_media(cls, event: SourceEvent) -> tuple[NormalizedMedia, ...]:
        result: list[NormalizedMedia] = []
        for message in event.messages:
            media = getattr(message, "media", None)
            if media is None:
                continue
            candidates = media if isinstance(media, (list, tuple)) else (media,)
            for index, item in enumerate(candidates):
                media_type, payload = cls._classify_media(item)
                result.append(
                    NormalizedMedia(
                        media_type=media_type,
                        identity=cls._media_identity(
                            item,
                            payload,
                            source_message_id=int(getattr(message, "id", 0) or 0),
                            item_index=index,
                            media_type=media_type,
                        ),
                        source_message_id=int(getattr(message, "id", 0) or 0),
                        file_name=cls._file_name(payload),
                        mime_type=cls._mime_type(payload),
                        byte_size=cls._byte_size(payload),
                    )
                )
        return tuple(result)

    @staticmethod
    def _classify_media(media: Any) -> tuple[str, Any]:
        declared = getattr(media, "media_type", None) or getattr(media, "type", None)
        if isinstance(declared, str) and declared.strip():
            return declared.strip().lower(), media

        name = type(media).__name__.lower()
        if "photo" in name:
            return "photo", getattr(media, "photo", media)

        payload = getattr(media, "document", media)
        payload_name = type(payload).__name__.lower()
        mime = ContentNormalizerV3._mime_type(payload) or ""
        attributes = getattr(payload, "attributes", ()) or ()

        for attribute in attributes:
            attribute_name = type(attribute).__name__.lower()
            if "audio" in attribute_name:
                return ("voice" if bool(getattr(attribute, "voice", False)) else "audio"), payload
            if "video" in attribute_name:
                return "video", payload

        if mime.startswith("video/") or "video" in payload_name:
            return "video", payload
        if mime.startswith("audio/") or "audio" in payload_name:
            return "audio", payload
        if "voice" in payload_name:
            return "voice", payload
        if "document" in name or "document" in payload_name:
            return "document", payload
        return "unknown", payload

    @staticmethod
    def _media_identity(
        media: Any,
        payload: Any,
        *,
        source_message_id: int,
        item_index: int,
        media_type: str,
    ) -> str:
        for candidate in (payload, media):
            for name in ("file_unique_id", "id", "document_id"):
                value = getattr(candidate, name, None)
                if value not in (None, ""):
                    return f"{name}:{value}"
        return f"source-message:{source_message_id}:{item_index}:{media_type}"

    @staticmethod
    def _file_name(payload: Any) -> str | None:
        value = getattr(payload, "file_name", None)
        if isinstance(value, str) and value:
            return value
        for attribute in getattr(payload, "attributes", ()) or ():
            candidate = getattr(attribute, "file_name", None)
            if isinstance(candidate, str) and candidate:
                return candidate
        return None

    @staticmethod
    def _mime_type(payload: Any) -> str | None:
        value = getattr(payload, "mime_type", None)
        return value if isinstance(value, str) and value else None

    @staticmethod
    def _byte_size(payload: Any) -> int | None:
        value = getattr(payload, "size", None)
        return int(value) if isinstance(value, int) and value >= 0 else None

    @staticmethod
    def _content_type(
        event: SourceEvent,
        text: str,
        media: tuple[NormalizedMedia, ...],
    ) -> ContentType:
        if event.grouped_id is not None and len(event.messages) > 1:
            return ContentType.ALBUM
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
