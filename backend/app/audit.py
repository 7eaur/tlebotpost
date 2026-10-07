"""Account-scoped audit logging helpers."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import AuditLog


class AuditRecorder:
    """Persist mutation audit records without storing secrets or content payloads."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        account_id: uuid.UUID,
    ) -> None:
        self.session_factory = session_factory
        self.account_id = account_id

    async def record(
        self,
        action: str,
        entity_type: str,
        *,
        entity_id: uuid.UUID | None = None,
        actor: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        safe_details = _sanitize(details or {})
        safe_details["actor"] = actor
        async with self.session_factory() as session:
            async with session.begin():
                session.add(
                    AuditLog(
                        account_id=self.account_id,
                        actor_user_id=None,
                        action=action[:120],
                        entity_type=entity_type[:120],
                        entity_id=entity_id,
                        details=safe_details,
                    )
                )


def _sanitize(value: Any) -> Any:
    secret_markers = {
        "password",
        "token",
        "secret",
        "api_hash",
        "bot_token",
        "phone_code_hash",
        "code",
    }
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).casefold()
            if any(marker in lowered for marker in secret_markers):
                result[str(key)] = "[redacted]"
            else:
                result[str(key)] = _sanitize(item)
        return result
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize(item) for item in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
