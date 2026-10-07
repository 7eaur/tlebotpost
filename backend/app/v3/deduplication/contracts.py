"""Pure contracts for V3 typed deduplication."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum


class FingerprintType(StrEnum):
    TELEGRAM_IDENTITY = "telegram_identity"
    TEXT = "text"
    MEDIA = "media"
    COMBINED = "combined"


class DeduplicationScope(StrEnum):
    ROUTE = "route"
    DESTINATION = "destination"


class DeduplicationDecision(StrEnum):
    READY_FOR_QUEUE = "ready_for_queue"
    DUPLICATE = "duplicate"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class DeduplicationPolicy:
    enabled: bool = True
    window_seconds: int = 86400
    compare_telegram_identity: bool = True
    compare_text: bool = True
    compare_media: bool = True
    scope: DeduplicationScope = DeduplicationScope.ROUTE


@dataclass(frozen=True, slots=True)
class FingerprintSignal:
    kind: FingerprintType
    value: str


@dataclass(frozen=True, slots=True)
class DeduplicationResult:
    execution_id: uuid.UUID
    decision: DeduplicationDecision
    reason_code: str
    matched_type: FingerprintType | None = None
    fingerprints: tuple[FingerprintSignal, ...] = ()
