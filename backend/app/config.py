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
