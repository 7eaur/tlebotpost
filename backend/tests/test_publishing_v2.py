from __future__ import annotations

from datetime import UTC, datetime, time
from types import SimpleNamespace

import pytest

from app.db.models import ScheduleKind, ScheduleProfile
from app.publishing.publisher import _send_method, _send_parameter
from app.publishing.scheduler import SchedulingError, next_run_at


def test_immediate_schedule_runs_now():
    now = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
    profile = ScheduleProfile(kind=ScheduleKind.IMMEDIATE)
    assert next_run_at(profile, now) == now


def test_interval_schedule_adds_interval():
    now = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
    profile = ScheduleProfile(kind=ScheduleKind.INTERVAL, interval_seconds=300)
    assert next_run_at(profile, now).hour == 12
    assert next_run_at(profile, now).minute == 5


def test_daily_window_returns_today_or_next_day():
    now = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)
    profile = ScheduleProfile(
        kind=ScheduleKind.DAILY_WINDOW,
        timezone="UTC",
        window_start=time(10, 30),
        window_end=time(12, 0),
    )
    assert next_run_at(profile, now) == datetime(2026, 10, 7, 10, 30, tzinfo=UTC)
    later = datetime(2026, 10, 7, 11, 0, tzinfo=UTC)
    assert next_run_at(profile, later) == later
    after = datetime(2026, 10, 7, 13, 0, tzinfo=UTC)
    assert next_run_at(profile, after).date().day == 8


def test_cron_returns_next_matching_time():
    profile = ScheduleProfile(kind=ScheduleKind.CRON, timezone="UTC", cron_expression="0 * * * *")
    now = datetime(2026, 10, 7, 12, 10, tzinfo=UTC)
    assert next_run_at(profile, now) == datetime(2026, 10, 7, 13, 0, tzinfo=UTC)


def test_invalid_cron_is_rejected():
    profile = ScheduleProfile(kind=ScheduleKind.CRON, cron_expression="not-a-cron")
    with pytest.raises(SchedulingError, match="cron"):
        next_run_at(profile, datetime.now(UTC))


def test_media_api_mapping_is_explicit():
    assert _send_method("photo") == "send_photo"
    assert _send_method("video") == "send_video"
    assert _send_method("unknown") == "send_document"
    assert _send_parameter("voice") == "voice"
    assert _send_parameter("unknown") == "document"


def test_publisher_result_reads_telegram_message_id():
    from app.publishing.publisher import _message_id

    assert _message_id(SimpleNamespace(message_id=99)) == 99
    assert _message_id(SimpleNamespace(id=100)) == 100


def test_message_id_is_required():
    from app.publishing.publisher import PublishError, _message_id

    with pytest.raises(PublishError, match="message id"):
        _message_id(SimpleNamespace())
