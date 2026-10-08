from __future__ import annotations

import json
import logging

from app.v3.observability import SafeJsonFormatter, SecretRedactor


def test_safe_json_formatter_redacts_configured_secrets_and_omits_extras():
    redactor = SecretRedactor(("bot-secret-token", "db-secret-pass"))
    formatter = SafeJsonFormatter(redactor)
    record = logging.LogRecord(
        name="app.v3.test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=10,
        msg="bot=bot-secret-token",
        args=(),
        exc_info=(
            RuntimeError,
            RuntimeError("database password db-secret-pass"),
            None,
        ),
    )
    record.message_text = "PRIVATE MESSAGE BODY"
    record.bot_token = "bot-secret-token"

    rendered = formatter.format(record)
    payload = json.loads(rendered)

    assert "bot-secret-token" not in rendered
    assert "db-secret-pass" not in rendered
    assert "PRIVATE MESSAGE BODY" not in rendered
    assert payload["message"] == "bot=[REDACTED]"
    assert payload["exception_type"] == "RuntimeError"
    assert "[REDACTED]" in payload["exception_message"]
    assert set(payload) == {
        "timestamp",
        "level",
        "logger",
        "message",
        "exception_type",
        "exception_message",
    }


def test_secret_redactor_prefers_longer_secret_first():
    redactor = SecretRedactor(("secret", "secret-token"))

    assert redactor.redact("secret-token secret") == "[REDACTED] [REDACTED]"
