"""Application settings loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


class ConfigurationError(ValueError):
    """Raised when required application settings are missing or invalid."""



def _csv(value: str) -> tuple[str, ...]:
    """Parse a comma-separated setting while dropping empty values."""
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _float_csv(value: str, default: tuple[float, ...]) -> tuple[float, ...]:
    if not value.strip():
        return default
    try:
        values = tuple(float(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as exc:
        raise ConfigurationError("RECONNECT_DELAYS must contain numbers") from exc
    return values or default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a number") from exc


@dataclass(frozen=True, slots=True)
class Settings:
    """Validated runtime configuration for the relay service."""

    api_id: int
    api_hash: str
    bot_token: str
    owner_id: int
    session_path: Path
    database_path: Path
    brand_footer: str
    brand_link: str
    include_keywords: tuple[str, ...] = ()
    exclude_keywords: tuple[str, ...] = ()
    log_level: str = "INFO"
    album_window_seconds: float = 0.8
    flood_wait_retries: int = 3
    send_interval_seconds: float = 1.1
    reconnect_delays: tuple[float, ...] = (5.0, 15.0, 30.0, 60.0)
    event_log_keep: int = 1000

    @classmethod
    def from_env(cls, env_file: str | Path | None = ".env") -> Settings:
        """Load and validate settings from environment variables."""
        if env_file is not None:
            load_dotenv(dotenv_path=env_file, override=False)

        values = {
            "api_id": _required_int("API_ID"),
            "api_hash": _required("API_HASH"),
            "bot_token": _required("BOT_TOKEN"),
            "owner_id": _required_int("OWNER_ID"),
            "session_path": Path(os.getenv("SESSION_PATH", "data/telegram_user.session")),
            "database_path": Path(os.getenv("DATABASE_PATH", "data/relay.sqlite3")),
            "brand_footer": os.getenv("BRAND_FOOTER", "").strip(),
            "brand_link": os.getenv("BRAND_LINK", "").strip(),
            "include_keywords": _csv(os.getenv("INCLUDE_KEYWORDS", "")),
            "exclude_keywords": _csv(os.getenv("EXCLUDE_KEYWORDS", "")),
            "log_level": os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
            "album_window_seconds": _env_float("ALBUM_WINDOW_SECONDS", 0.8),
            "flood_wait_retries": _env_int("FLOOD_WAIT_RETRIES", 3),
            "send_interval_seconds": _env_float("SEND_INTERVAL_SECONDS", 1.1),
            "reconnect_delays": _float_csv(
                os.getenv("RECONNECT_DELAYS", ""), (5.0, 15.0, 30.0, 60.0)
            ),
            "event_log_keep": _env_int("EVENT_LOG_KEEP", 1000),
        }
        settings = cls(**values)
        settings.validate()
        return settings

    def validate(self) -> None:
        """Validate cross-field constraints after construction."""
        if self.api_id <= 0:
            raise ConfigurationError("API_ID must be a positive integer")
        if self.owner_id <= 0:
            raise ConfigurationError("OWNER_ID must be a positive integer")
        if not self.brand_footer and not self.brand_link:
            raise ConfigurationError("BRAND_FOOTER or BRAND_LINK must be configured")
        if self.log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ConfigurationError("LOG_LEVEL must be a standard logging level")
        if self.album_window_seconds <= 0:
            raise ConfigurationError("ALBUM_WINDOW_SECONDS must be positive")
        if self.flood_wait_retries < 0:
            raise ConfigurationError("FLOOD_WAIT_RETRIES must be non-negative")
        if self.send_interval_seconds < 0:
            raise ConfigurationError("SEND_INTERVAL_SECONDS must be non-negative")
        if not self.reconnect_delays or any(delay <= 0 for delay in self.reconnect_delays):
            raise ConfigurationError("RECONNECT_DELAYS must be positive")
        if self.event_log_keep < 0:
            raise ConfigurationError("EVENT_LOG_KEEP must be non-negative")


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigurationError(f"{name} is required")
    return value


def _required_int(name: str) -> int:
    value = _required(name)
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
