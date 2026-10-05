"""Pure content transformation and filtering rules."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.models import RelaySettings

_URL_RE = re.compile(
    r"(?:https?://|www\.|t\.me/|telegram\.me/)[^\s<>]+",
    re.IGNORECASE,
)
_TELEGRAM_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me)/[^\s<>]+",
    re.IGNORECASE,
)
_RIGHTS_LINE_RE = re.compile(
    r"^\s*(?:حقوق(?:نا| النشر)?|الحقوق|المصدر|source|credit|credits|via)\s*[:：\-]?",
    re.IGNORECASE,
)
_SEPARATOR_RE = re.compile(r"^\s*(?:[-_=ـ─━—–·•♦️]){8,}\s*$")
_PLAIN_SIGNATURE_RE = re.compile(r"^[A-Za-z0-9_#@.\-]{3,40}$")
_EMOJI_RE = re.compile(
    "["
    "\U0001F1E6-\U0001F1FF"
    "\U0001F300-\U0001FAFF"
    "\u2300-\u23FF"
    "\u2600-\u27BF"
    "]|[\u200D\uFE0E\uFE0F]"
)


@dataclass(frozen=True, slots=True)
class TransformResult:
    """Output of filtering and transformation."""

    text: str | None
    skipped_reason: str | None = None

    @property
    def should_publish(self) -> bool:
        return self.skipped_reason is None


class ContentTransformer:
    """Apply deterministic, configurable rules to source text."""

    def __init__(self, settings: RelaySettings) -> None:
        self.settings = settings

    def transform(
        self,
        text: str | None,
        *,
        media_types: Sequence[str] = (),
    ) -> TransformResult:
        """Filter a message and append the fixed ownership footer/link."""
        original = text or ""
        normalized_types = tuple(dict.fromkeys(media_types))
        if self._contains_excluded_keyword(original):
            return TransformResult(None, "excluded_keyword")
        if self.settings.include_keywords and not self._contains_included_keyword(original):
            return TransformResult(None, "missing_included_keyword")
        if self.settings.allowed_media_types:
            if not normalized_types or any(
                media_type not in self.settings.allowed_media_types
                for media_type in normalized_types
            ):
                return TransformResult(None, "disallowed_media_type")

        cleaned = self._clean_text(original)
        if not cleaned and not normalized_types:
            return TransformResult(None, "empty_after_cleaning")
        branded = self._append_branding(cleaned)
        return TransformResult(branded or None)

    def _contains_excluded_keyword(self, text: str) -> bool:
        lowered = text.casefold()
        return any(keyword.casefold() in lowered for keyword in self.settings.exclude_keywords)

    def _contains_included_keyword(self, text: str) -> bool:
        lowered = text.casefold()
        return any(keyword.casefold() in lowered for keyword in self.settings.include_keywords)

    def _clean_text(self, text: str) -> str:
        original_lines = text.splitlines()
        footer_start = self._source_footer_start(original_lines)
        lines: list[str] = []
        for index, line in enumerate(original_lines):
            if footer_start is not None and index >= footer_start:
                continue
            if _RIGHTS_LINE_RE.match(line):
                continue
            if self._is_configured_branding_line(line):
                continue
            line = _URL_RE.sub("", line)
            line = re.sub(r"[ \t]{2,}", " ", line)
            line = line.rstrip()
            if line.strip():
                lines.append(line)
            elif lines and lines[-1] != "":
                lines.append("")
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        lines = self._remove_repeated_plain_signatures(lines)
        return "\n".join(lines).strip()

    def _source_footer_start(self, lines: Sequence[str]) -> int | None:
        """Find a trailing source signature made of a separator and stamp."""
        separator_indexes = [
            index for index, line in enumerate(lines) if _SEPARATOR_RE.match(line)
        ]
        for index in reversed(separator_indexes):
            tail = lines[index + 1 :]
            if any(_TELEGRAM_URL_RE.search(line) for line in tail):
                return self._footer_block_start(lines, index)
            normalized_tail = [line.strip() for line in tail if line.strip()]
            if any(
                _PLAIN_SIGNATURE_RE.fullmatch(line)
                and normalized_tail.count(line) >= 2
                for line in normalized_tail
            ):
                return self._footer_block_start(lines, index)
        return None

    @staticmethod
    def _footer_block_start(lines: Sequence[str], separator_index: int) -> int:
        """Include standalone decorative symbols immediately before the separator."""
        start = separator_index
        while start > 0:
            candidate = lines[start - 1].strip()
            if not candidate or re.search(r"[\w\u0600-\u06ff]", candidate):
                break
            start -= 1
        return start

    @staticmethod
    def _remove_repeated_plain_signatures(lines: list[str]) -> list[str]:
        """Remove repeated ASCII-only source stamps, usually appended by repost bots."""
        counts: dict[str, int] = {}
        for line in lines:
            normalized = line.strip()
            if _PLAIN_SIGNATURE_RE.fullmatch(normalized):
                counts[normalized] = counts.get(normalized, 0) + 1
        repeated = {
            value for value, count in counts.items() if count >= 2
        }
        if not repeated:
            return lines
        last_indexes = {
            value: max(index for index, line in enumerate(lines) if line.strip() == value)
            for value in repeated
        }
        return [
            line
            for index, line in enumerate(lines)
            if not (
                line.strip() in repeated
                and last_indexes[line.strip()] - index <= 7
            )
        ]

    def _is_configured_branding_line(self, line: str) -> bool:
        """Avoid duplicating our own footer when a source already includes it."""
        normalized = line.strip()
        return bool(
            normalized
            and (
                (self.settings.brand_link and self.settings.brand_link in normalized)
                or (
                    self.settings.brand_footer
                    and normalized == self.settings.brand_footer.strip()
                )
            )
        )

    def _append_branding(self, text: str) -> str:
        branding = [part for part in (self.settings.brand_footer, self.settings.brand_link) if part]
        if not branding:
            return text
        branding_text = "\n".join(branding)
        return "\n\n".join(part for part in (text, branding_text) if part).strip()
