"""Telegram ingestion adapter layer for V3."""

from .adapter import TelegramUserAdapter, TelethonUserAdapter
from .albums import AlbumCollectorV3
from .ingestion import IngestionSource, TelegramIngestionComponent
from .snapshots import SourceEventSnapshotError, SourceEventSnapshotStore
from .types import SourceEvent, SourceMediaSnapshot, SourceMessageSnapshot

__all__ = [
    "AlbumCollectorV3",
    "IngestionSource",
    "SourceEvent",
    "SourceEventSnapshotError",
    "SourceEventSnapshotStore",
    "SourceMediaSnapshot",
    "SourceMessageSnapshot",
    "TelegramIngestionComponent",
    "TelegramUserAdapter",
    "TelethonUserAdapter",
]
