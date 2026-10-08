"""Transactional V3 deduplication between content processing and the publish queue."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    DeduplicationProfile,
    Destination,
    RouteExecution,
    RouteExecutionStatus,
    RouteFingerprint,
    SourceRoute,
)
from app.v3.content import ProcessingDecision, ProcessingResult, RoutePayloadStore
from app.v3.domain import RouteExecutionService, SourceCheckpointCoordinator

from .contracts import (
    DeduplicationDecision,
    DeduplicationPolicy,
    DeduplicationResult,
    DeduplicationScope,
    FingerprintSignal,
    FingerprintType,
)
from .fingerprints import FingerprintBuilderV3

_UNIQUE_REASON = "dedup_unique"
_DISABLED_REASON = "dedup_disabled"
_NO_SIGNALS_REASON = "dedup_no_signals"
_FAILED_REASON = "deduplication_error"
_MATCH_REASON = {
    FingerprintType.TELEGRAM_IDENTITY: "duplicate_exact_identity",
    FingerprintType.TEXT: "duplicate_matching_text",
    FingerprintType.MEDIA: "duplicate_matching_media",
    FingerprintType.COMBINED: "duplicate_matching_combined",
}
_MATCH_PRIORITY = (
    FingerprintType.TELEGRAM_IDENTITY,
    FingerprintType.TEXT,
    FingerprintType.MEDIA,
    FingerprintType.COMBINED,
)
ReadyForQueueHandler = Callable[[DeduplicationResult], Awaitable[object]]


class DeduplicationError(RuntimeError):
    """Raised when a V3 route cannot be deduplicated under a valid contract."""


class DeduplicationPolicyResolver:
    """Resolve route policy first, then destination fallback, with explicit scope."""

    def __init__(self, account_id: uuid.UUID) -> None:
        self.account_id = account_id

    async def resolve(
        self,
        session: AsyncSession,
        execution: RouteExecution,
    ) -> DeduplicationPolicy:
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
            raise DeduplicationError("dedup_route_unavailable")

        profile_id = route.deduplication_profile_id or destination.deduplication_profile_id
        if profile_id is None:
            return DeduplicationPolicy()

        profile = await session.scalar(
            select(DeduplicationProfile).where(
                DeduplicationProfile.id == profile_id,
                DeduplicationProfile.account_id == self.account_id,
            )
        )
        if profile is None:
            raise DeduplicationError("dedup_profile_unavailable")
        if profile.window_seconds <= 0:
            raise DeduplicationError("dedup_window_must_be_positive")

        options = profile.options if isinstance(profile.options, dict) else {}
        scope_value = options.get("scope", DeduplicationScope.ROUTE.value)
        try:
            scope = DeduplicationScope(str(scope_value).strip().lower())
        except ValueError as exc:
            raise DeduplicationError("dedup_scope_invalid") from exc

        return DeduplicationPolicy(
            enabled=profile.enabled,
            window_seconds=profile.window_seconds,
            compare_telegram_identity=profile.compare_telegram_id,
            compare_text=profile.compare_text,
            compare_media=profile.compare_media,
            scope=scope,
        )


class DeduplicationCoordinator:
    """Atomically check typed fingerprints and persist the Phase-5 route outcome."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        account_id: uuid.UUID,
        *,
        clock: Callable[[], datetime] | None = None,
        on_ready: ReadyForQueueHandler | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.account_id = account_id
        self.clock = clock or (lambda: datetime.now(UTC))
        self.policies = DeduplicationPolicyResolver(account_id)
        self.payloads = RoutePayloadStore()
        self.on_ready = on_ready
        self._logger = logging.getLogger(__name__)

    async def process(self, result: ProcessingResult) -> DeduplicationResult:
        if result.decision is not ProcessingDecision.READY_FOR_DEDUP or result.content is None:
            raise DeduplicationError("processing_result_not_ready_for_dedup")

        try:
            outcome = await self._process_ready(result)
        except Exception:
            self._logger.exception(
                "V3 deduplication failed: execution_id=%s",
                result.execution_id,
            )
            return await self._mark_failed(result.execution_id)

        if (
            outcome.decision is DeduplicationDecision.READY_FOR_QUEUE
            and self.on_ready is not None
        ):
            try:
                await self.on_ready(outcome)
            except Exception:
                self._logger.exception(
                    "V3 queue handoff deferred for recovery: execution_id=%s",
                    outcome.execution_id,
                )
        return outcome

    async def recover_pending(self, *, limit: int = 100) -> int:
        if limit <= 0:
            raise DeduplicationError("limit must be positive")
        async with self.session_factory() as session:
            executions = list(
                (
                    await session.scalars(
                        select(RouteExecution)
                        .where(
                            RouteExecution.account_id == self.account_id,
                            RouteExecution.status == RouteExecutionStatus.READY_FOR_DEDUP,
                        )
                        .order_by(RouteExecution.created_at, RouteExecution.id)
                        .limit(limit)
                    )
                ).all()
            )

        recovered = 0
        for execution in executions:
            async with self.session_factory() as session:
                payload = await self.payloads.get(
                    session,
                    account_id=self.account_id,
                    execution_id=execution.id,
                )
            if payload is None:
                await self._mark_failed(execution.id)
                continue
            processing_result = ProcessingResult(
                execution_id=execution.id,
                decision=ProcessingDecision.READY_FOR_DEDUP,
                reason_code=execution.reason_code or "ready_for_dedup",
                content=self.payloads.to_content(payload),
            )
            await self.process(processing_result)
            recovered += 1
        return recovered

    async def _process_ready(self, result: ProcessingResult) -> DeduplicationResult:
        observed_at = _as_utc(self.clock())
        async with self.session_factory() as session:
            async with session.begin():
                execution = await session.scalar(
                    select(RouteExecution)
                    .where(
                        RouteExecution.id == result.execution_id,
                        RouteExecution.account_id == self.account_id,
                    )
                    .with_for_update()
                )
                if execution is None:
                    raise DeduplicationError("route_execution_unavailable")

                if execution.status is RouteExecutionStatus.READY_FOR_QUEUE:
                    return DeduplicationResult(
                        execution_id=execution.id,
                        decision=DeduplicationDecision.READY_FOR_QUEUE,
                        reason_code=execution.reason_code or _UNIQUE_REASON,
                    )
                if execution.status is RouteExecutionStatus.DUPLICATE:
                    return DeduplicationResult(
                        execution_id=execution.id,
                        decision=DeduplicationDecision.DUPLICATE,
                        reason_code=execution.reason_code or "duplicate",
                    )
                if execution.status is not RouteExecutionStatus.READY_FOR_DEDUP:
                    raise DeduplicationError("route_execution_not_ready_for_dedup")

                policy = await self.policies.resolve(session, execution)
                service = RouteExecutionService(session, self.account_id)

                if not policy.enabled:
                    await service.transition(
                        execution.id,
                        RouteExecutionStatus.READY_FOR_QUEUE,
                        reason_code=_DISABLED_REASON,
                    )
                    return DeduplicationResult(
                        execution_id=execution.id,
                        decision=DeduplicationDecision.READY_FOR_QUEUE,
                        reason_code=_DISABLED_REASON,
                    )

                signals = FingerprintBuilderV3.build(
                    source_id=execution.source_id,
                    event_key=execution.event_key,
                    content=result.content,
                    policy=policy,
                )
                if not signals:
                    await service.transition(
                        execution.id,
                        RouteExecutionStatus.READY_FOR_QUEUE,
                        reason_code=_NO_SIGNALS_REASON,
                    )
                    return DeduplicationResult(
                        execution_id=execution.id,
                        decision=DeduplicationDecision.READY_FOR_QUEUE,
                        reason_code=_NO_SIGNALS_REASON,
                    )

                scope_key = _scope_key(execution, policy)
                await self._lock_signals(session, scope_key, signals)
                matched = await self._find_match(
                    session,
                    execution=execution,
                    scope_key=scope_key,
                    signals=signals,
                    policy=policy,
                    observed_at=observed_at,
                )
                await self._record_signals(
                    session,
                    execution=execution,
                    scope_key=scope_key,
                    signals=signals,
                    observed_at=observed_at,
                )

                if matched is not None:
                    reason = _MATCH_REASON[matched]
                    await service.transition(
                        execution.id,
                        RouteExecutionStatus.DUPLICATE,
                        reason_code=reason,
                    )
                    await SourceCheckpointCoordinator(
                        session,
                        self.account_id,
                    ).try_advance(
                        source_id=execution.source_id,
                        cursor_message_id=execution.cursor_message_id,
                        event_at=observed_at,
                    )
                    return DeduplicationResult(
                        execution_id=execution.id,
                        decision=DeduplicationDecision.DUPLICATE,
                        reason_code=reason,
                        matched_type=matched,
                        fingerprints=signals,
                    )

                await service.transition(
                    execution.id,
                    RouteExecutionStatus.READY_FOR_QUEUE,
                    reason_code=_UNIQUE_REASON,
                )
                return DeduplicationResult(
                    execution_id=execution.id,
                    decision=DeduplicationDecision.READY_FOR_QUEUE,
                    reason_code=_UNIQUE_REASON,
                    fingerprints=signals,
                )

    async def _find_match(
        self,
        session: AsyncSession,
        *,
        execution: RouteExecution,
        scope_key: str,
        signals: tuple[FingerprintSignal, ...],
        policy: DeduplicationPolicy,
        observed_at: datetime,
    ) -> FingerprintType | None:
        by_type = {signal.kind: signal for signal in signals}
        cutoff = observed_at - timedelta(seconds=policy.window_seconds)

        for kind in _MATCH_PRIORITY:
            signal = by_type.get(kind)
            if signal is None:
                continue
            statement = select(RouteFingerprint.id).where(
                RouteFingerprint.account_id == self.account_id,
                RouteFingerprint.scope_key == scope_key,
                RouteFingerprint.fingerprint_type == kind.value,
                RouteFingerprint.fingerprint == signal.value,
                RouteFingerprint.route_execution_id != execution.id,
            )
            if kind is not FingerprintType.TELEGRAM_IDENTITY:
                statement = statement.where(RouteFingerprint.observed_at >= cutoff)
            if await session.scalar(statement.limit(1)) is not None:
                return kind
        return None

    async def _record_signals(
        self,
        session: AsyncSession,
        *,
        execution: RouteExecution,
        scope_key: str,
        signals: tuple[FingerprintSignal, ...],
        observed_at: datetime,
    ) -> None:
        existing = set(
            (
                await session.scalars(
                    select(RouteFingerprint.fingerprint_type).where(
                        RouteFingerprint.account_id == self.account_id,
                        RouteFingerprint.route_execution_id == execution.id,
                    )
                )
            ).all()
        )
        for signal in signals:
            if signal.kind.value in existing:
                continue
            session.add(
                RouteFingerprint(
                    account_id=self.account_id,
                    route_execution_id=execution.id,
                    scope_key=scope_key,
                    fingerprint_type=signal.kind.value,
                    fingerprint=signal.value,
                    observed_at=observed_at,
                )
            )
        await session.flush()

    async def _lock_signals(
        self,
        session: AsyncSession,
        scope_key: str,
        signals: tuple[FingerprintSignal, ...],
    ) -> None:
        keys = sorted(
            f"{self.account_id}:{scope_key}:{signal.kind.value}:{signal.value}"
            for signal in signals
        )
        for key in keys:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": key},
            )

    async def _mark_failed(self, execution_id: uuid.UUID) -> DeduplicationResult:
        async with self.session_factory() as session:
            async with session.begin():
                service = RouteExecutionService(session, self.account_id)
                execution = await service.executions.get(execution_id)
                if execution is None:
                    raise DeduplicationError("route_execution_unavailable")
                if execution.status is RouteExecutionStatus.READY_FOR_DEDUP:
                    await service.transition(
                        execution.id,
                        RouteExecutionStatus.FAILED,
                        reason_code=_FAILED_REASON,
                    )
                    await SourceCheckpointCoordinator(
                        session,
                        self.account_id,
                    ).try_advance(
                        source_id=execution.source_id,
                        cursor_message_id=execution.cursor_message_id,
                        event_at=_as_utc(self.clock()),
                    )
                return DeduplicationResult(
                    execution_id=execution.id,
                    decision=DeduplicationDecision.FAILED,
                    reason_code=execution.reason_code or _FAILED_REASON,
                )


def _scope_key(execution: RouteExecution, policy: DeduplicationPolicy) -> str:
    if policy.scope is DeduplicationScope.DESTINATION:
        return f"destination:{execution.destination_id}"
    return f"route:{execution.route_id}"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise DeduplicationError("dedup_clock_must_be_timezone_aware")
    return value.astimezone(UTC)
