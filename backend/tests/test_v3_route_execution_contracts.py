from __future__ import annotations

import uuid

import pytest

from app.db import RouteExecutionStatus
from app.v3.domain import (
    CHECKPOINT_SAFE_STATUSES,
    FINAL_EXECUTION_STATUSES,
    RouteExecutionError,
    build_event_key,
)


def test_event_key_prefers_album_identity():
    assert build_event_key(message_id=42, grouped_id=9001) == "group:9001"


def test_event_key_uses_message_identity_without_album():
    assert build_event_key(message_id=42, grouped_id=None) == "message:42"


@pytest.mark.parametrize(
    ("message_id", "grouped_id"),
    [
        (None, None),
        (0, None),
        (None, 0),
        (1, -5),
    ],
)
def test_event_key_rejects_invalid_identity(message_id, grouped_id):
    with pytest.raises(RouteExecutionError):
        build_event_key(message_id=message_id, grouped_id=grouped_id)


def test_checkpoint_safe_statuses_are_explicit():
    assert RouteExecutionStatus.RECEIVED not in CHECKPOINT_SAFE_STATUSES
    assert RouteExecutionStatus.PROCESSING not in CHECKPOINT_SAFE_STATUSES
    assert RouteExecutionStatus.QUEUED in CHECKPOINT_SAFE_STATUSES
    assert RouteExecutionStatus.FILTERED in CHECKPOINT_SAFE_STATUSES
    assert RouteExecutionStatus.DUPLICATE in CHECKPOINT_SAFE_STATUSES
    assert RouteExecutionStatus.PUBLISHED in CHECKPOINT_SAFE_STATUSES
    assert RouteExecutionStatus.FAILED in CHECKPOINT_SAFE_STATUSES


def test_queued_is_checkpoint_safe_but_not_final():
    assert RouteExecutionStatus.QUEUED in CHECKPOINT_SAFE_STATUSES
    assert RouteExecutionStatus.QUEUED not in FINAL_EXECUTION_STATUSES


def test_route_execution_error_is_runtime_error():
    assert issubclass(RouteExecutionError, RuntimeError)
    assert uuid.uuid4() != uuid.uuid4()
