"""Telegram ingestion adapter layer for V3."""

from .adapter import TelegramUserAdapter, TelethonUserAdapter
from .albums import AlbumCollectorV3
from .ingestion import IngestionSource, TelegramIngestionComponent
from .types import SourceEvent

__all__ = [
    "AlbumCollectorV3",
    "IngestionSource",
    "SourceEvent",
    "TelegramIngestionComponent",
    "TelegramUserAdapter",
    "TelethonUserAdapter",
]
