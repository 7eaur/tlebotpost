"""Rule-based content classification for Runtime v2."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Category, ClassificationPolicy, ContentCategory


@dataclass(frozen=True, slots=True)
class CategoryMatch:
    category_id: uuid.UUID
    confidence: float
    rule_index: int | None


class ClassificationEngine:
    """Evaluate deterministic user-defined classification rules.

    Rule format is intentionally data-driven and stable:
    {
      "category_id": "<uuid>",
      "keywords": ["عاجل", "تقنية"],
      "hashtags": ["#اليمن"],
      "source_ids": ["<uuid>"],
      "match": "any" | "all",
      "confidence": 1.0
    }
    """

    async def classify(
        self,
        session: AsyncSession,
        *,
        account_id: uuid.UUID,
        policy: ClassificationPolicy | None,
        text: str,
        source_id: uuid.UUID,
    ) -> tuple[CategoryMatch, ...]:
        if policy is None or not policy.enabled:
            return ()

        valid_categories = {
            row.id
            for row in (
                await session.scalars(
                    select(Category).where(Category.account_id == account_id)
                )
            ).all()
        }
        matches: dict[uuid.UUID, CategoryMatch] = {}
        text_folded = text.casefold()
        hashtags = {tag.casefold() for tag in re.findall(r"#[\w\u0600-\u06ff_]+", text)}

        for index, raw_rule in enumerate(policy.rules or []):
            if not isinstance(raw_rule, dict):
                continue
            category_id = _uuid(raw_rule.get("category_id"))
            if category_id is None or category_id not in valid_categories:
                continue
            checks: list[bool] = []
            keywords = _strings(raw_rule.get("keywords"))
            if keywords:
                checks.append(
                    any(keyword.casefold() in text_folded for keyword in keywords)
                )
            rule_hashtags = _strings(raw_rule.get("hashtags"))
            if rule_hashtags:
                checks.append(
                    any(
                        _normalize_hashtag(tag).casefold() in hashtags
                        for tag in rule_hashtags
                    )
                )
            source_ids = {
                value for value in (_uuid(item) for item in _values(raw_rule.get("source_ids"))) if value
            }
            if source_ids:
                checks.append(source_id in source_ids)
            if not checks:
                continue
            mode = str(raw_rule.get("match", "any")).lower()
            matched = all(checks) if mode == "all" else any(checks)
            if not matched:
                continue
            confidence = _confidence(raw_rule.get("confidence"))
            current = matches.get(category_id)
            if current is None or confidence > current.confidence:
                matches[category_id] = CategoryMatch(category_id, confidence, index)

        if not matches and policy.default_category_id in valid_categories:
            default_id = policy.default_category_id
            if default_id is not None:
                matches[default_id] = CategoryMatch(default_id, 1.0, None)

        return tuple(matches.values())

    @staticmethod
    async def persist(
        session: AsyncSession,
        *,
        content_item_id: uuid.UUID,
        matches: tuple[CategoryMatch, ...],
    ) -> None:
        for match in matches:
            session.add(
                ContentCategory(
                    content_item_id=content_item_id,
                    category_id=match.category_id,
                    confidence=match.confidence,
                    assigned_by_rule=match.rule_index is not None,
                )
            )


def _values(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _strings(value: Any) -> list[str]:
    return [item.strip() for item in _values(value) if isinstance(item, str) and item.strip()]


def _uuid(value: Any) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    if not isinstance(value, str):
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def _confidence(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 1.0
    return max(0.0, min(number, 1.0))


def _normalize_hashtag(value: str) -> str:
    return value if value.startswith("#") else f"#{value}"
