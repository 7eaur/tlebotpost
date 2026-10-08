"""Central configuration for the V3 runtime."""

from __future__ import annotations

import os
import uuid
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
    """Credentials and local session location for Telegram user ingestion."""

    api_id: int
    api_hash: str
    session_path: Path

    @classmethod
    def from_env(cls) -> TelegramV3Settings:
        try:
            values = cls(
                api_id=int(_required("API_ID")),
                api_hash=_required("API_HASH"),
                session_path=Path(
                    os.getenv("V3_SESSION_PATH", os.getenv("SESSION_PATH", "data/v3.session"))
                ),
            )
        except V3ConfigurationError:
            raise
        except ValueError as exc:
            raise V3ConfigurationError("API_ID must be an integer") from exc
        if values.api_id <= 0:
            raise V3ConfigurationError("API_ID must be positive")
        return values


@dataclass(frozen=True, slots=True)
class IngestionV3Settings:
    """Database scope and live-listener behavior for Telegram ingestion."""

    account_id: uuid.UUID
    telegram_account_id: uuid.UUID
    album_window_seconds: float = 0.8
    reconnect_delays: tuple[float, ...] = (5.0, 15.0, 30.0, 60.0)

    @classmethod
    def from_env(cls) -> IngestionV3Settings:
        try:
            values = cls(
                account_id=uuid.UUID(_required("V3_ACCOUNT_ID")),
                telegram_account_id=uuid.UUID(_required("V3_TELEGRAM_ACCOUNT_ID")),
                album_window_seconds=float(os.getenv("V3_ALBUM_WINDOW_SECONDS", "0.8")),
                reconnect_delays=_float_tuple_env(
                    "V3_RECONNECT_DELAYS",
                    (5.0, 15.0, 30.0, 60.0),
                ),
            )
        except V3ConfigurationError:
            raise
        except (TypeError, ValueError) as exc:
            raise V3ConfigurationError("invalid V3 ingestion environment value") from exc
        if values.album_window_seconds <= 0:
            raise V3ConfigurationError("V3_ALBUM_WINDOW_SECONDS must be positive")
        if not values.reconnect_delays or any(delay <= 0 for delay in values.reconnect_delays):
            raise V3ConfigurationError("V3_RECONNECT_DELAYS must contain positive values")
        return values


@dataclass(frozen=True, slots=True)
class PublisherV3Settings:
    """Bot API worker and transient media staging settings."""

    bot_token: str
    worker_id: str = "publisher-v3"
    poll_interval_seconds: float = 1.0
    queue_batch_size: int = 10
    lease_seconds: int = 120
    send_interval_seconds: float = 1.1
    staging_path: Path = Path("data/v3-media-staging")
    staging_stale_seconds: int = 21600

    @classmethod
    def from_env(cls) -> PublisherV3Settings:
        try:
            values = cls(
                bot_token=_required("BOT_TOKEN"),
                worker_id=os.getenv("V3_PUBLISHER_WORKER_ID", "publisher-v3").strip()
                or "publisher-v3",
                poll_interval_seconds=float(
                    os.getenv("V3_PUBLISHER_POLL_INTERVAL_SECONDS", "1")
                ),
                queue_batch_size=int(os.getenv("V3_PUBLISHER_BATCH_SIZE", "10")),
                lease_seconds=int(os.getenv("V3_PUBLISHER_LEASE_SECONDS", "120")),
                send_interval_seconds=float(
                    os.getenv("V3_PUBLISHER_SEND_INTERVAL_SECONDS", "1.1")
                ),
                staging_path=Path(
                    os.getenv("V3_MEDIA_STAGING_PATH", "data/v3-media-staging")
                ),
                staging_stale_seconds=int(
                    os.getenv("V3_MEDIA_STAGING_STALE_SECONDS", "21600")
                ),
            )
        except V3ConfigurationError:
            raise
        except (TypeError, ValueError) as exc:
            raise V3ConfigurationError("invalid V3 publisher environment value") from exc
        if values.poll_interval_seconds <= 0:
            raise V3ConfigurationError(
                "V3_PUBLISHER_POLL_INTERVAL_SECONDS must be positive"
            )
        if values.queue_batch_size <= 0:
            raise V3ConfigurationError("V3_PUBLISHER_BATCH_SIZE must be positive")
        if values.lease_seconds <= 0:
            raise V3ConfigurationError("V3_PUBLISHER_LEASE_SECONDS must be positive")
        if values.send_interval_seconds < 0:
            raise V3ConfigurationError(
                "V3_PUBLISHER_SEND_INTERVAL_SECONDS cannot be negative"
            )
        if values.staging_stale_seconds <= 0:
            raise V3ConfigurationError("V3_MEDIA_STAGING_STALE_SECONDS must be positive")
        if not values.worker_id:
            raise V3ConfigurationError("V3_PUBLISHER_WORKER_ID is required")
        return values


@dataclass(frozen=True, slots=True)
class ControlV3Settings:
    """Owner-only Telegram control surface settings."""

    bot_token: str
    owner_id: int

    @classmethod
    def from_env(cls) -> ControlV3Settings:
        token = (
            os.getenv("V3_CONTROL_BOT_TOKEN", "").strip()
            or os.getenv("BOT_TOKEN", "").strip()
        )
        if not token:
            raise V3ConfigurationError(
                "V3_CONTROL_BOT_TOKEN or BOT_TOKEN is required when V3 control is enabled"
            )
        owner_raw = (
            os.getenv("V3_CONTROL_OWNER_ID", "").strip()
            or os.getenv("OWNER_ID", "").strip()
        )
        if not owner_raw:
            raise V3ConfigurationError(
                "V3_CONTROL_OWNER_ID or OWNER_ID is required when V3 control is enabled"
            )
        try:
            owner_id = int(owner_raw)
        except ValueError as exc:
            raise V3ConfigurationError("V3 control owner id must be an integer") from exc
        if owner_id <= 0:
            raise V3ConfigurationError("V3 control owner id must be positive")
        return cls(bot_token=token, owner_id=owner_id)


@dataclass(frozen=True, slots=True)
class RuntimeV3Settings:
    """All process-level settings needed by the V3 application foundation."""

    database: DatabaseSettings
    environment: RuntimeEnvironment = RuntimeEnvironment.DEVELOPMENT
    log_level: str = "INFO"
    telegram: TelegramV3Settings | None = None
    ingestion: IngestionV3Settings | None = None
    publisher: PublisherV3Settings | None = None
    control: ControlV3Settings | None = None

    @classmethod
    def from_env(
        cls,
        env_file: str | Path | None = ".env",
        *,
        require_telegram: bool | None = None,
    ) -> RuntimeV3Settings:
        if env_file is not None:
            load_dotenv(env_file, override=False)

        raw_environment = os.getenv("APP_ENV", "development").strip().lower()
        try:
            environment = RuntimeEnvironment(raw_environment)
        except ValueError as exc:
            raise V3ConfigurationError(
                "APP_ENV must be one of: development, test, production"
            ) from exc

        publisher_enabled = _bool_env("V3_PUBLISHER_ENABLED", False)
        control_enabled = _bool_env("V3_CONTROL_ENABLED", False)
        telegram_requested = (
            _bool_env("V3_TELEGRAM_ENABLED", False)
            if require_telegram is None
            else require_telegram
        )
        telegram_enabled = telegram_requested or publisher_enabled or control_enabled
        values = cls(
            database=DatabaseSettings.from_env(),
            environment=environment,
            log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
            telegram=TelegramV3Settings.from_env() if telegram_enabled else None,
            ingestion=IngestionV3Settings.from_env() if telegram_enabled else None,
            publisher=PublisherV3Settings.from_env() if publisher_enabled else None,
            control=ControlV3Settings.from_env() if control_enabled else None,
        )
        values.validate()
        return values

    def validate(self) -> None:
        allowed_levels = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
        if self.log_level not in allowed_levels:
            raise V3ConfigurationError(
                "LOG_LEVEL must be one of CRITICAL, ERROR, WARNING, INFO, DEBUG"
            )
        if (self.telegram is None) != (self.ingestion is None):
            raise V3ConfigurationError(
                "V3 Telegram and ingestion settings must be enabled together"
            )
        if self.publisher is not None and (
            self.telegram is None or self.ingestion is None
        ):
            raise V3ConfigurationError(
                "V3 publisher requires Telegram ingestion settings"
            )
        if self.control is not None and (
            self.telegram is None or self.ingestion is None
        ):
            raise V3ConfigurationError(
                "V3 control requires Telegram ingestion settings"
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


def _float_tuple_env(name: str, default: tuple[float, ...]) -> tuple[float, ...]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return tuple(float(part.strip()) for part in raw.split(",") if part.strip())
    except ValueError as exc:
        raise V3ConfigurationError(f"{name} must be a comma-separated number list") from exc
