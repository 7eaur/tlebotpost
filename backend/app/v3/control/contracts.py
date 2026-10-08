"""Typed control-plane views for V3."""

from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProjectView:
    id: uuid.UUID
    name: str
    slug: str
    status: str


@dataclass(frozen=True, slots=True)
class SourceView:
    id: uuid.UUID
    title: str
    chat_id: int
    username: str | None
    status: str
    last_seen_message_id: int


@dataclass(frozen=True, slots=True)
class DestinationView:
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    chat_id: int
    username: str | None
    status: str
    publishing_mode: str


@dataclass(frozen=True, slots=True)
class RouteView:
    id: uuid.UUID
    source_id: uuid.UUID
    destination_id: uuid.UUID
    status: str
    priority: int


@dataclass(frozen=True, slots=True)
class ControlStatus:
    telegram_status: str
    telegram_connected: bool
    telegram_last_error: str | None
    projects_total: int
    projects_active: int
    sources_total: int
    sources_active: int
    destinations_total: int
    destinations_active: int
    routes_total: int
    routes_active: int
    queue_pending: int
    queue_failed: int
    last_publish_error: str | None
