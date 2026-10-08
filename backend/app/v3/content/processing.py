"""Route-aware V3 content processing between ingestion and deduplication."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    BrandingProfile,
    Destination,
    FilterProfile,
    RouteExecution,
    RouteExecutionStatus,
    SourceRoute,
    TransformProfile,
)
from app.v3.domain import EventRegistration, RouteExecutionService, SourceCheckpointCoordinator
from app.v3.telegram.snapshots import SourceEventSnapshotStore
from app.v3.telegram.types import SourceEvent

from .contracts import (
    BrandingSpec,
    ProcessedContent,
    ProcessingDecision,
    ProcessingResult,
    RouteContentPolicy,
)
from .normalization import ContentNormalizerV3
from .persistence import RoutePayloadStore

_READY_REASON = "ready_for_dedup"
_FAILED_REASON = "content_processing_error"
_ProfileT = TypeVar("_ProfileT", BrandingProfile, FilterProfile, TransformProfile)
ReadyHandler = Callable[[ProcessingResult], Awaitable[Any]]


class ContentProcessingError(RuntimeError):
    """Raised when a durable route cannot be processed under a valid route policy."""


class RoutePolicyResolver:
    """Resolve route-local policy with destination branding fallback."""

    def __init__(self, account_id: uuid.UUID) -> None:
        self.account_id = account_id

    async def resolve(
        self,
        session: AsyncSession,
        execution: RouteExecution,
    ) -> RouteContentPolicy:
        route = await session.scalar(
            select(SourceRoute).where(
                SourceRoute.id == execution.route_id,
                SourceRoute.account_id == self.account_id,
                SourceRoute.source_id == execution.source_id,
                SourceRoute.destination_id == execution.destination_id,
            )
        )
        destination = await session.scalar(
            select(Destination).where(
                Destination.id == execution.destination_id,
                Destination.account_id == self.account_id,
            )
        )
        if route is None or destination is None:
            raise ContentProcessingError("route_policy_unavailable")

        filter_profile = await self._profile(
            session,
            FilterProfile,
            route.filter_profile_id,
            "filter_profile",
        )
        transform_profile = await self._profile(
            session,
            TransformProfile,
            route.transform_profile_id,
            "transform_profile",
        )
        branding_id = route.branding_profile_id or destination.branding_profile_id
        branding_profile = await self._profile(
            session,
            BrandingProfile,
            branding_id,
            "branding_profile",
        )

        custom_rules = _dict_value(filter_profile.custom_rules if filter_profile else None)
        transform_options = _dict_value(transform_profile.options if transform_profile else None)
        remove_urls = filter_profile.remove_urls if filter_profile is not None else True

        max_blank_lines = transform_options.get("max_blank_lines", 1)
        if not isinstance(max_blank_lines, int) or isinstance(max_blank_lines, bool):
            max_blank_lines = 1

        return RouteContentPolicy(
            include_keywords=_string_tuple(
                filter_profile.include_keywords if filter_profile is not None else None
            ),
            exclude_keywords=_string_tuple(
                filter_profile.exclude_keywords if filter_profile is not None else None
            ),
            allowed_media_types=frozenset(
                value.casefold()
                for value in _string_tuple(
                    filter_profile.allowed_media_types if filter_profile is not None else None
                )
            ),
            remove_telegram_urls=_bool_rule(
                custom_rules,
                "remove_telegram_urls",
                remove_urls,
            ),
            remove_general_urls=_bool_rule(
                custom_rules,
                "remove_general_urls",
                remove_urls,
            ),
            remove_source_rights=(
                filter_profile.remove_source_rights if filter_profile is not None else True
            ),
            preserve_emoji=(filter_profile.preserve_emoji if filter_profile is not None else True),
            preserve_line_breaks=(
                transform_profile.preserve_line_breaks if transform_profile is not None else True
            ),
            trim_whitespace=(
                transform_profile.trim_whitespace if transform_profile is not None else True
            ),
            max_blank_lines=max(0, min(max_blank_lines, 3)),
            branding=_branding_spec(branding_profile),
        )

    async def _profile(
        self,
        session: AsyncSession,
        model: type[_ProfileT],
        profile_id: uuid.UUID | None,
        label: str,
    ) -> _ProfileT | None:
        if profile_id is None:
            return None
        profile = await session.scalar(
            select(model).where(
                model.id == profile_id,
                model.account_id == self.account_id,
            )
        )
        if profile is None:
            raise ContentProcessingError(f"{label}_unavailable")
        return profile


class ContentFilterV3:
    """Apply deterministic route filters to already-normalized content."""

    @staticmethod
    def reason(content: ProcessedContent, policy: RouteContentPolicy) -> str | None:
        haystack = content.normalized_text.casefold()
        if policy.include_keywords and not any(
            keyword.casefold() in haystack for keyword in policy.include_keywords
        ):
            return "missing_include_keyword"
        if any(keyword.casefold() in haystack for keyword in policy.exclude_keywords):
            return "matched_exclude_keyword"
        if policy.allowed_media_types and any(
            item.media_type.casefold() not in policy.allowed_media_types for item in content.media
        ):
            return "media_type_not_allowed"
        if not content.normalized_text and not content.media:
            return "empty_content"
        return None


class BrandingRendererV3:
    """Render route/destination branding after filters without changing dedup text."""

    @staticmethod
    def render(content: ProcessedContent, policy: RouteContentPolicy) -> ProcessedContent:
        branding = policy.branding
        if not branding.enabled:
            return content

        suffix = [
            value.strip()
            for value in (branding.footer, branding.link)
            if value and value.strip()
        ]
        if not suffix:
            return content

        parts: list[str] = []
        if content.normalized_text:
            parts.append(content.normalized_text)
        if branding.separator and branding.separator.strip() and parts:
            parts.append(branding.separator.strip())
        parts.extend(suffix)
        return ProcessedContent(
            normalized_text=content.normalized_text,
            rendered_text="\n".join(parts).strip(),
            content_type=content.content_type,
            media=content.media,
        )


class ContentProcessingCoordinator:
    """Process each registered route independently and persist only route state."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        account_id: uuid.UUID,
        *,
        normalizer: ContentNormalizerV3 | None = None,
        filters: ContentFilterV3 | None = None,
        branding: BrandingRendererV3 | None = None,
        on_ready: ReadyHandler | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.account_id = account_id
        self.normalizer = normalizer or ContentNormalizerV3()
        self.filters = filters or ContentFilterV3()
        self.branding = branding or BrandingRendererV3()
        self.policies = RoutePolicyResolver(account_id)
        self.payloads = RoutePayloadStore()
        self.source_snapshots = SourceEventSnapshotStore()
        self.on_ready = on_ready
        self._logger = logging.getLogger(__name__)

    async def process_registration(
        self,
        event: SourceEvent,
        registration: EventRegistration,
    ) -> tuple[ProcessingResult, ...]:
        self._validate_registration(event, registration)
        results: list[ProcessingResult] = []
        for registered in registration.executions:
            result = await self._process_execution(event, registered.id)
            results.append(result)
            if result.decision is ProcessingDecision.READY_FOR_DEDUP and self.on_ready is not None:
                await self.on_ready(result)
        return tuple(results)

    async def recover_pending(self, *, limit: int = 100) -> int:
        if limit <= 0:
            raise ContentProcessingError("limit must be positive")
        async with self.session_factory() as session:
            rows = (
                await session.execute(
                    select(
                        RouteExecution.source_id,
                        RouteExecution.event_key,
                        RouteExecution.cursor_message_id,
                    )
                    .where(
                        RouteExecution.account_id == self.account_id,
                        RouteExecution.status.in_(
                            (
                                RouteExecutionStatus.RECEIVED,
                                RouteExecutionStatus.PROCESSING,
                            )
                        ),
                    )
                    .order_by(RouteExecution.created_at, RouteExecution.id)
                    .limit(limit)
                )
            ).all()

        recovered = 0
        seen: set[tuple[uuid.UUID, str]] = set()
        for source_id, event_key, cursor_message_id in rows:
            key = (source_id, event_key)
            if key in seen:
                continue
            seen.add(key)
            try:
                async with self.session_factory() as session:
                    event = await self.source_snapshots.restore(
                        session,
                        account_id=self.account_id,
                        source_id=source_id,
                        event_key=event_key,
                    )
                    executions = tuple(
                        (
                            await session.scalars(
                                select(RouteExecution)
                                .where(
                                    RouteExecution.account_id == self.account_id,
                                    RouteExecution.source_id == source_id,
                                    RouteExecution.event_key == event_key,
                                )
                                .order_by(RouteExecution.created_at, RouteExecution.id)
                            )
                        ).all()
                    )
                registration = EventRegistration(
                    source_id=source_id,
                    event_key=event_key,
                    cursor_message_id=cursor_message_id,
                    executions=executions,
                )
                await self.process_registration(event, registration)
            except Exception:
                self._logger.exception(
                    "V3 content recovery blocked: source_id=%s event_key=%s",
                    source_id,
                    event_key,
                )
                continue
            recovered += 1
        return recovered

    async def _process_execution(
        self,
        event: SourceEvent,
        execution_id: uuid.UUID,
    ) -> ProcessingResult:
        try:
            async with self.session_factory() as session:
                async with session.begin():
                    service = RouteExecutionService(session, self.account_id)
                    execution = await session.scalar(
                        select(RouteExecution)
                        .where(
                            RouteExecution.id == execution_id,
                            RouteExecution.account_id == self.account_id,
                        )
                        .with_for_update()
                    )
                    if execution is None:
                        raise ContentProcessingError("route_execution_unavailable")
                    self._validate_execution(event, execution)

                    if execution.status is RouteExecutionStatus.READY_FOR_DEDUP:
                        payload = await self.payloads.get(
                            session,
                            account_id=self.account_id,
                            execution_id=execution.id,
                        )
                        if payload is None:
                            raise ContentProcessingError("route_payload_missing")
                        restored = self.payloads.to_content(payload)
                        return ProcessingResult(
                            execution_id=execution.id,
                            decision=ProcessingDecision.READY_FOR_DEDUP,
                            reason_code=execution.reason_code or _READY_REASON,
                            content=restored,
                        )

                    if execution.status in {
                        RouteExecutionStatus.FILTERED,
                        RouteExecutionStatus.DUPLICATE,
                        RouteExecutionStatus.READY_FOR_QUEUE,
                        RouteExecutionStatus.QUEUED,
                        RouteExecutionStatus.PUBLISHED,
                        RouteExecutionStatus.FAILED,
                        RouteExecutionStatus.CANCELLED,
                    }:
                        return ProcessingResult(
                            execution_id=execution.id,
                            decision=(
                                ProcessingDecision.FILTERED
                                if execution.status is RouteExecutionStatus.FILTERED
                                else ProcessingDecision.FAILED
                            ),
                            reason_code=execution.reason_code or execution.status.value,
                        )

                    if execution.status is RouteExecutionStatus.RECEIVED:
                        await service.transition(execution.id, RouteExecutionStatus.PROCESSING)
                    elif execution.status not in {
                        RouteExecutionStatus.PROCESSING,
                        RouteExecutionStatus.READY_FOR_DEDUP,
                    }:
                        raise ContentProcessingError("route_execution_not_processable")

                    policy = await self.policies.resolve(session, execution)
                    normalized = self.normalizer.normalize(event, policy)
                    filter_reason = self.filters.reason(normalized, policy)
                    if filter_reason is not None:
                        if execution.status is RouteExecutionStatus.READY_FOR_DEDUP:
                            raise ContentProcessingError("ready_execution_cannot_be_refiltered")
                        await service.transition(
                            execution.id,
                            RouteExecutionStatus.FILTERED,
                            reason_code=filter_reason,
                        )
                        await SourceCheckpointCoordinator(
                            session,
                            self.account_id,
                        ).try_advance(
                            source_id=event.source_id,
                            cursor_message_id=event.cursor_message_id,
                            event_at=event.received_at,
                        )
                        return ProcessingResult(
                            execution_id=execution.id,
                            decision=ProcessingDecision.FILTERED,
                            reason_code=filter_reason,
                        )

                    rendered = self.branding.render(normalized, policy)
                    await self.payloads.persist(
                        session,
                        account_id=self.account_id,
                        execution_id=execution.id,
                        content=rendered,
                    )
                    await service.transition(
                        execution.id,
                        RouteExecutionStatus.READY_FOR_DEDUP,
                        reason_code=_READY_REASON,
                    )
                    return ProcessingResult(
                        execution_id=execution.id,
                        decision=ProcessingDecision.READY_FOR_DEDUP,
                        reason_code=_READY_REASON,
                        content=rendered,
                    )
        except Exception:
            self._logger.exception(
                "V3 content processing failed: execution_id=%s source_id=%s",
                execution_id,
                event.source_id,
            )
            return await self._mark_failed(event, execution_id)

    async def _mark_failed(
        self,
        event: SourceEvent,
        execution_id: uuid.UUID,
    ) -> ProcessingResult:
        async with self.session_factory() as session:
            async with session.begin():
                service = RouteExecutionService(session, self.account_id)
                execution = await service.executions.get(execution_id)
                if execution is None:
                    raise ContentProcessingError("route_execution_unavailable")
                if execution.status in {
                    RouteExecutionStatus.RECEIVED,
                    RouteExecutionStatus.PROCESSING,
                    RouteExecutionStatus.READY_FOR_DEDUP,
                }:
                    await service.transition(
                        execution.id,
                        RouteExecutionStatus.FAILED,
                        reason_code=_FAILED_REASON,
                    )
                    await SourceCheckpointCoordinator(
                        session,
                        self.account_id,
                    ).try_advance(
                        source_id=event.source_id,
                        cursor_message_id=event.cursor_message_id,
                        event_at=event.received_at,
                    )
                return ProcessingResult(
                    execution_id=execution.id,
                    decision=ProcessingDecision.FAILED,
                    reason_code=execution.reason_code or _FAILED_REASON,
                )

    def _validate_registration(
        self,
        event: SourceEvent,
        registration: EventRegistration,
    ) -> None:
        if event.account_id != self.account_id:
            raise ContentProcessingError("event_account_mismatch")
        if registration.source_id != event.source_id:
            raise ContentProcessingError("registration_source_mismatch")
        if registration.cursor_message_id != event.cursor_message_id:
            raise ContentProcessingError("registration_cursor_mismatch")

    def _validate_execution(self, event: SourceEvent, execution: RouteExecution) -> None:
        if execution.account_id != self.account_id:
            raise ContentProcessingError("execution_account_mismatch")
        if execution.source_id != event.source_id:
            raise ContentProcessingError("execution_source_mismatch")
        if execution.cursor_message_id != event.cursor_message_id:
            raise ContentProcessingError("execution_cursor_mismatch")


def _branding_spec(profile: BrandingProfile | None) -> BrandingSpec:
    if profile is None or not profile.enabled:
        return BrandingSpec()
    return BrandingSpec(
        enabled=True,
        footer=profile.footer,
        link=profile.link,
        separator=profile.separator,
    )


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item.strip() for item in value if isinstance(item, str) and item.strip())


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _bool_rule(rules: dict[str, Any], key: str, default: bool) -> bool:
    value = rules.get(key)
    return value if isinstance(value, bool) else default
