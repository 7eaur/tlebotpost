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
_RIGHTS_LINE_RE = re.compile(
    r"^\s*(?:حقوق(?:نا| النشر)?|الحقوق|المصدر|source|credit|credits|via)\s*[:：\-]?",
    re.IGNORECASE,
)
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
        lines: list[str] = []
        for line in text.splitlines():
            if _RIGHTS_LINE_RE.match(line):
                continue
            line = _URL_RE.sub("", line)
            line = _EMOJI_RE.sub("", line)
            line = re.sub(r"[ \t]+", " ", line).strip()
            if line:
                lines.append(line)
        return "\n".join(lines).strip()

    def _append_branding(self, text: str) -> str:
        branding = [part for part in (self.settings.brand_footer, self.settings.brand_link) if part]
        if not branding:
            return text
        return "\n\n".join(part for part in (text, *branding) if part).strip()
