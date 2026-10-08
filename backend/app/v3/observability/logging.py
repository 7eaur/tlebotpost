"""Secret-safe structured logging for V3."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from datetime import UTC, datetime
from urllib.parse import urlsplit

from app.v3.config import RuntimeV3Settings


class SecretRedactor:
    """Replace configured secret values before they reach a log sink."""

    def __init__(self, secrets: Iterable[str]) -> None:
        values = {value for value in secrets if value and len(value) >= 4}
        self._secrets = tuple(sorted(values, key=len, reverse=True))

    def redact(self, value: object) -> str:
        text = str(value)
        for secret in self._secrets:
            text = text.replace(secret, "[REDACTED]")
        return text


class SafeJsonFormatter(logging.Formatter):
    """Emit only an allow-listed record shape; never serialize LogRecord extras."""

    def __init__(self, redactor: SecretRedactor) -> None:
        super().__init__()
        self.redactor = redactor

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": self.redactor.redact(record.getMessage()),
        }
        if record.exc_info is not None:
            exc_type, exc_value, _traceback = record.exc_info
            payload["exception_type"] = getattr(exc_type, "__name__", "Exception")
            payload["exception_message"] = self.redactor.redact(exc_value)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class SafeTextFormatter(logging.Formatter):
    """Readable formatter with the same redaction and no traceback serialization."""

    def __init__(self, redactor: SecretRedactor) -> None:
        super().__init__()
        self.redactor = redactor

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.now(UTC).isoformat()
        message = self.redactor.redact(record.getMessage())
        rendered = f"{timestamp} {record.levelname} {record.name} {message}"
        if record.exc_info is not None:
            exc_type, exc_value, _traceback = record.exc_info
            name = getattr(exc_type, "__name__", "Exception")
            rendered += f" exception={name}:{self.redactor.redact(exc_value)}"
        return rendered


def configure_v3_logging(settings: RuntimeV3Settings) -> None:
    """Replace root handlers with the V3 secret-safe formatter."""
    redactor = SecretRedactor(_configured_secrets(settings))
    handler = logging.StreamHandler()
    if settings.log_format == "json":
        handler.setFormatter(SafeJsonFormatter(redactor))
    else:
        handler.setFormatter(SafeTextFormatter(redactor))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, settings.log_level))
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def _configured_secrets(settings: RuntimeV3Settings) -> tuple[str, ...]:
    secrets: list[str] = [settings.database.url]
    try:
        parsed = urlsplit(settings.database.url)
    except ValueError:
        parsed = None
    if parsed is not None and parsed.password:
        secrets.append(parsed.password)
    if settings.telegram is not None:
        secrets.append(settings.telegram.api_hash)
    if settings.publisher is not None:
        secrets.append(settings.publisher.bot_token)
    if settings.control is not None:
        secrets.append(settings.control.bot_token)
    return tuple(secrets)
