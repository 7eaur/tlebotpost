"""Central configuration for the V3 runtime."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from dotenv import load_dotenv

from app.db.config import DatabaseSettings


class V3ConfigurationError(ValueError):
    """Raised when V3 application configuration is invalid."""


class RuntimeEnvironment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


@dataclass(frozen=True, slots=True)
class TelegramV3Settings:
    api_id: int
    api_hash: str
    bot_token: str
    owner_id: int
    session_path: Path

    @classmethod
    def from_env(cls) -> "TelegramV3Settings":
        try:
            values = cls(
                api_id=int(_required("API_ID")),
                api_hash=_required("API_HASH"),
                bot_token=_required("BOT_TOKEN"),
                owner_id=int(_required("OWNER_ID")),
                session_path=Path(
                    os.getenv("V3_SESSION_PATH", os.getenv("SESSION_PATH", "data/v3.session"))
                ),
            )
        except V3ConfigurationError:
            raise
        except ValueError as exc:
            raise V3ConfigurationError("API_ID and OWNER_ID must be integers") from exc
        if values.api_id <= 0:
            raise V3ConfigurationError("API_ID must be positive")
        if values.owner_id <= 0:
            raise V3ConfigurationError("OWNER_ID must be positive")
        return values


@dataclass(frozen=True, slots=True)
class RuntimeV3Settings:
    """All process-level settings needed by the V3 application foundation."""

    database: DatabaseSettings
    environment: RuntimeEnvironment = RuntimeEnvironment.DEVELOPMENT
    log_level: str = "INFO"
    telegram: TelegramV3Settings | None = None

    @classmethod
    def from_env(
        cls,
        env_file: str | Path | None = ".env",
        *,
        require_telegram: bool | None = None,
    ) -> "RuntimeV3Settings":
        if env_file is not None:
            load_dotenv(env_file, override=False)

        raw_environment = os.getenv("APP_ENV", "development").strip().lower()
        try:
            environment = RuntimeEnvironment(raw_environment)
        except ValueError as exc:
            raise V3ConfigurationError(
                "APP_ENV must be one of: development, test, production"
            ) from exc

        telegram_enabled = (
            _bool_env("V3_TELEGRAM_ENABLED", False)
            if require_telegram is None
            else require_telegram
        )
        values = cls(
            database=DatabaseSettings.from_env(),
            environment=environment,
            log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
            telegram=TelegramV3Settings.from_env() if telegram_enabled else None,
        )
        values.validate()
        return values

    def validate(self) -> None:
        allowed_levels = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
        if self.log_level not in allowed_levels:
            raise V3ConfigurationError(
                "LOG_LEVEL must be one of CRITICAL, ERROR, WARNING, INFO, DEBUG"
            )


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise V3ConfigurationError(f"{name} is required when V3 Telegram is enabled")
    return value


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name, str(default)).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise V3ConfigurationError(f"{name} must be a boolean")
