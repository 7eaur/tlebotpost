"""Durable route-specific processed payload persistence for V3."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RoutePublishPayload

from .contracts import NormalizedMedia, ProcessedContent


class RoutePayloadError(RuntimeError):
    """Raised when a persisted route payload is missing or malformed."""


class RoutePayloadStore:
    """Persist the first deterministic processed payload for one RouteExecution."""

    async def persist(
        self,
        session: AsyncSession,
        *,
        account_id: uuid.UUID,
        execution_id: uuid.UUID,
        content: ProcessedContent,
    ) -> RoutePublishPayload:
        existing = await self.get(
            session,
            account_id=account_id,
            execution_id=execution_id,
        )
        if existing is not None:
            return existing

        payload = RoutePublishPayload(
            account_id=account_id,
            route_execution_id=execution_id,
            content_type=content.content_type,
            normalized_text=content.normalized_text or None,
            rendered_text=content.rendered_text or None,
            media_json=[
                {
                    "media_type": item.media_type,
                    "identity": item.identity,
                    "source_message_id": item.source_message_id,
                    "file_name": item.file_name,
                    "mime_type": item.mime_type,
                    "byte_size": item.byte_size,
                }
                for item in content.media
            ],
        )
        session.add(payload)
        await session.flush()
        return payload

    async def get(
        self,
        session: AsyncSession,
        *,
        account_id: uuid.UUID,
        execution_id: uuid.UUID,
    ) -> RoutePublishPayload | None:
        return await session.scalar(
            select(RoutePublishPayload).where(
                RoutePublishPayload.account_id == account_id,
                RoutePublishPayload.route_execution_id == execution_id,
            )
        )

    def to_content(self, payload: RoutePublishPayload) -> ProcessedContent:
        media_value = payload.media_json
        if not isinstance(media_value, list):
            raise RoutePayloadError("route_payload_media_invalid")

        media: list[NormalizedMedia] = []
        for item in media_value:
            if not isinstance(item, dict):
                raise RoutePayloadError("route_payload_media_item_invalid")
            media_type = item.get("media_type")
            identity = item.get("identity")
            source_message_id = item.get("source_message_id")
            if not isinstance(media_type, str) or not media_type:
                raise RoutePayloadError("route_payload_media_type_invalid")
            if not isinstance(identity, str) or not identity:
                raise RoutePayloadError("route_payload_media_identity_invalid")
            if not isinstance(source_message_id, int):
                raise RoutePayloadError("route_payload_media_source_id_invalid")
            media.append(
                NormalizedMedia(
                    media_type=media_type,
                    identity=identity,
                    source_message_id=source_message_id,
                    file_name=_optional_str(item.get("file_name")),
                    mime_type=_optional_str(item.get("mime_type")),
                    byte_size=_optional_int(item.get("byte_size")),
                )
            )

        return ProcessedContent(
            normalized_text=payload.normalized_text or "",
            rendered_text=payload.rendered_text or "",
            content_type=payload.content_type,
            media=tuple(media),
        )


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and value >= 0 else None
