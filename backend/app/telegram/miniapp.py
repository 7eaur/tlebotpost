"""Telegram Mini App initData verification helpers."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl


class MiniAppAuthError(ValueError):
    """Raised when Telegram WebApp init data is missing or invalid."""


@dataclass(frozen=True, slots=True)
class MiniAppUser:
    id: int
    first_name: str
    last_name: str | None = None
    username: str | None = None


def verify_init_data(
    init_data: str,
    *,
    bot_token: str,
    expected_user_id: int,
    max_age_seconds: int = 900,
) -> MiniAppUser:
    """Verify Telegram WebApp initData and return the authorized owner."""
    if not init_data:
        raise MiniAppAuthError("missing_init_data")

    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", "")
    if not received_hash:
        raise MiniAppAuthError("missing_hash")

    data_check_string = "\n".join(
        f"{key}={value}" for key, value in sorted(pairs.items())
    )
    secret_key = hmac.new(
        b"WebAppData",
        bot_token.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    calculated_hash = hmac.new(
        secret_key,
        data_check_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(received_hash, calculated_hash):
        raise MiniAppAuthError("invalid_hash")

    auth_date_raw = pairs.get("auth_date", "")
    try:
        auth_date = int(auth_date_raw)
    except ValueError as exc:
        raise MiniAppAuthError("invalid_auth_date") from exc
    now = int(time.time())
    if auth_date > now + 30 or now - auth_date > max_age_seconds:
        raise MiniAppAuthError("expired_init_data")

    try:
        user_data = json.loads(pairs.get("user", "{}"))
        user_id = int(user_data["id"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise MiniAppAuthError("invalid_user") from exc
    if user_id != expected_user_id:
        raise MiniAppAuthError("unauthorized_user")

    return MiniAppUser(
        id=user_id,
        first_name=str(user_data.get("first_name") or ""),
        last_name=(
            str(user_data["last_name"])
            if user_data.get("last_name") is not None
            else None
        ),
        username=(
            str(user_data["username"])
            if user_data.get("username") is not None
            else None
        ),
    )
