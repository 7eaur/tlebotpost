"""PostgreSQL connection settings for the v2 data layer."""

from __future__ import annotations

import os
from dataclasses import dataclass


class DatabaseConfigurationError(ValueError):
    """Raised when PostgreSQL configuration is invalid."""


@dataclass(frozen=True, slots=True)
class DatabaseSettings:
    """Validated settings used to create the async SQLAlchemy engine."""

    url: str
    pool_size: int = 5
    max_overflow: int = 10
    pool_timeout: float = 30.0
    pool_recycle: int = 1800
    echo: bool = False

    @classmethod
    def from_env(cls) -> DatabaseSettings:
        url = os.getenv("DATABASE_URL", "").strip()
        if not url:
            raise DatabaseConfigurationError("DATABASE_URL is required for the v2 database")
        values = cls(
            url=url,
            pool_size=_int_env("DATABASE_POOL_SIZE", 5),
            max_overflow=_int_env("DATABASE_MAX_OVERFLOW", 10),
            pool_timeout=_float_env("DATABASE_POOL_TIMEOUT", 30.0),
            pool_recycle=_int_env("DATABASE_POOL_RECYCLE", 1800),
            echo=_bool_env("DATABASE_ECHO", False),
        )
        values.validate()
        return values

    def validate(self) -> None:
        if not self.url.startswith("postgresql+asyncpg://"):
            raise DatabaseConfigurationError(
                "DATABASE_URL must use the postgresql+asyncpg:// SQLAlchemy scheme"
            )
        if self.pool_size < 1:
            raise DatabaseConfigurationError("DATABASE_POOL_SIZE must be positive")
        if self.max_overflow < 0:
            raise DatabaseConfigurationError("DATABASE_MAX_OVERFLOW cannot be negative")
        if self.pool_timeout <= 0:
            raise DatabaseConfigurationError("DATABASE_POOL_TIMEOUT must be positive")
        if self.pool_recycle < 0:
            raise DatabaseConfigurationError("DATABASE_POOL_RECYCLE cannot be negative")


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise DatabaseConfigurationError(f"{name} must be an integer") from exc


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError as exc:
        raise DatabaseConfigurationError(f"{name} must be a number") from exc


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name, str(default)).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise DatabaseConfigurationError(f"{name} must be a boolean")
