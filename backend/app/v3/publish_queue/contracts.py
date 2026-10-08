"""Contracts and pure retry helpers for the V3 publish queue."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.db.models import JobStatus


class QueueHandoffDecision(StrEnum):
    ENQUEUED = "enqueued"
    MANUAL_HOLD = "manual_hold"
    ALREADY_ENQUEUED = "already_enqueued"


@dataclass(frozen=True, slots=True)
class QueueHandoffResult:
    execution_id: uuid.UUID
    job_id: uuid.UUID
    decision: QueueHandoffDecision
    job_status: JobStatus
    scheduled_for: datetime


def retry_delay_seconds(
    *,
    attempt_count: int,
    base_seconds: int,
    cap_seconds: int,
    retry_after_seconds: int | None = None,
) -> int:
    if attempt_count <= 0:
        raise ValueError("attempt_count must be positive")
    if base_seconds <= 0:
        raise ValueError("base_seconds must be positive")
    if cap_seconds < base_seconds:
        raise ValueError("cap_seconds must be at least base_seconds")
    if retry_after_seconds is not None and retry_after_seconds <= 0:
        raise ValueError("retry_after_seconds must be positive")

    exponent = min(attempt_count - 1, 30)
    backoff = min(base_seconds * (2**exponent), cap_seconds)
    if retry_after_seconds is not None:
        return max(backoff, retry_after_seconds)
    return backoff
