"""Typed V3 operational metrics and diagnostics."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class AttemptDiagnostic:
    attempt_number: int
    status: str
    error_code: str | None
    telegram_message_id: int | None
    latency_ms: int | None
    started_at: datetime
    finished_at: datetime | None


@dataclass(frozen=True, slots=True)
class JobDiagnostic:
    job_id: uuid.UUID
    job_status: str
    route_execution_id: uuid.UUID | None
    route_status: str | None
    route_reason_code: str | None
    destination_id: uuid.UUID
    source_route_id: uuid.UUID | None
    attempt_count: int
    max_attempts: int
    last_error_code: str | None
    locked_by: str | None
    scheduled_for: datetime
    next_attempt_at: datetime | None
    published_at: datetime | None
    telegram_message_ids: tuple[int, ...]
    attempts: tuple[AttemptDiagnostic, ...]


@dataclass(frozen=True, slots=True)
class RuntimeMetrics:
    jobs_by_status: dict[str, int]
    executions_by_status: dict[str, int]
    attempts_total: int
    system_events_total: int
