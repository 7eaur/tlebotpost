"""Deterministic scheduling helpers for publish jobs."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from croniter import croniter

from app.db.models import ScheduleKind, ScheduleProfile


class SchedulingError(ValueError):
    """Raised when a schedule profile cannot produce a valid execution time."""


def next_run_at(profile: ScheduleProfile | None, now: datetime | None = None) -> datetime:
    """Return the first valid execution time for a profile."""
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    if profile is None or profile.kind == ScheduleKind.IMMEDIATE:
        return current
    try:
        timezone = ZoneInfo(profile.timezone or "UTC")
    except Exception as exc:
        raise SchedulingError(f"invalid schedule timezone: {profile.timezone}") from exc

    local_now = current.astimezone(timezone)
    if profile.kind == ScheduleKind.INTERVAL:
        seconds = profile.interval_seconds
        if not seconds or seconds <= 0:
            raise SchedulingError("interval schedule requires interval_seconds")
        return current + timedelta(seconds=seconds)

    if profile.kind == ScheduleKind.DAILY_WINDOW:
        if profile.window_start is None or profile.window_end is None:
            raise SchedulingError("daily window requires window_start and window_end")
        start = _at_local_time(local_now, profile.window_start)
        end = _at_local_time(local_now, profile.window_end)
        if end < start:
            end += timedelta(days=1)
            if local_now < start:
                previous_start = start - timedelta(days=1)
                previous_end = end - timedelta(days=1)
                if previous_start <= local_now <= previous_end:
                    return current
        if local_now < start:
            return start.astimezone(UTC)
        if start <= local_now <= end:
            return current
        return (start + timedelta(days=1)).astimezone(UTC)

    if profile.kind == ScheduleKind.CRON:
        expression = (profile.cron_expression or "").strip()
        if not expression:
            raise SchedulingError("cron schedule requires cron_expression")
        try:
            candidate = croniter(expression, local_now).get_next(datetime)
        except (ValueError, KeyError) as exc:
            raise SchedulingError("invalid cron expression") from exc
        if candidate.tzinfo is None:
            candidate = candidate.replace(tzinfo=timezone)
        return candidate.astimezone(UTC)

    raise SchedulingError(f"unsupported schedule kind: {profile.kind}")


def _at_local_time(value: datetime, target: time) -> datetime:
    return value.replace(
        hour=target.hour,
        minute=target.minute,
        second=target.second,
        microsecond=target.microsecond,
    )
