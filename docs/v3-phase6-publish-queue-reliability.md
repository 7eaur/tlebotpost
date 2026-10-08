# V3 Phase 6 — Publish Queue and Reliability

Date: 2026-10-08  
Branch: `rebuild/v3-foundation-20261007`  
Verified code head: `d58cac213f9f87afb0a0b3f611e4dbb998ebb9d8`  
CI run: `37707590801` — success

## Goal

Make the handoff from accepted Telegram events through processing and deduplication to the publish queue durable across crashes and restarts, then provide reliable queue claim/lease/retry semantics without implementing Telegram publishing itself.

## Resulting V3 path

```text
Telegram SourceEvent
      |
      | durable registration transaction
      v
SourceEventSnapshot + RouteExecution(received) + last_seen
      |
      v
Content Processing
      |
      v
RoutePublishPayload + ready_for_dedup
      |
      v
Deduplication
      |
      v
ready_for_queue
      |
      v
PublishJob + RouteExecution(queued)
      |
      v
lease / retry / recovery
      |
      v
Phase 7 Publisher
```

## Durable acceptance snapshot

Phase 3 previously persisted RouteExecution and last_seen before invoking content processing.

That left a crash window: a process failure after registration but before Phase 4 could leave an accepted event at `received` with no reconstructable in-process Telegram message object.

Phase 6 closes that window with `source_event_snapshots`.

The snapshot is written in the same PostgreSQL transaction as:
- RouteExecution registration;
- source checkpoint seen-cursor advancement.

Persisted inputs:
- account/source;
- event key;
- chat id;
- cursor / primary message id;
- grouped id;
- received timestamp;
- ordered message records;
- raw text/caption;
- media identity/type/metadata required to reproduce deterministic Phase 4 inputs.

The snapshot does not trigger Telegram history replay.

## Route-specific durable payload

`route_publish_payloads` stores the deterministic output of Phase 4 per RouteExecution.

It contains:
- content type;
- normalized text;
- rendered text;
- ordered media descriptors.

The payload is inserted before the same transaction changes the execution to `ready_for_dedup`.

Therefore:
- `ready_for_dedup` always has a recoverable processed payload;
- Phase 5 can resume after restart;
- route-specific transforms/branding do not need to be reconstructed from shared V2 ContentItem rows.

## PublishJob reuse

Phase 6 extends the existing `publish_jobs` table instead of creating a parallel queue.

V3-specific fields:
- `route_execution_id`;
- `route_payload_id`;
- `max_attempts`;
- `lease_expires_at`.

`content_item_id` becomes nullable because V3 route-specific publish payloads are not forced into V2 ContentItem persistence.

V3 enforces one job per RouteExecution.

V3 claim/recovery selects only jobs where both V3 references are populated, so V3 workers do not consume legacy V2 jobs.

## Idempotent enqueue

The queue handoff locks the RouteExecution.

If a V3 PublishJob already exists for that execution:
- no new job is created;
- a stale `ready_for_queue` execution can be repaired to `queued`;
- the same job id is returned.

A new job is created before RouteExecution transitions to `queued`.

Because `queued` is checkpoint-safe, committed cursor advancement occurs only after the durable job exists.

## Publishing modes

### Direct

Immediate `queued` job.

### Scheduled

Uses the existing schedule resolver and persists the calculated `scheduled_for`.

### Manual

Adds `manual_hold`.

Manual jobs:
- are durable;
- make the route durably represented;
- are not claimable by workers;
- become queued only through explicit release.

## Claim contract

Due V3 jobs are claimed with:

`SELECT ... FOR UPDATE SKIP LOCKED`

Claimable states:
- queued;
- retry_wait whose retry time has arrived.

On claim:
- status -> processing;
- attempt_count increments;
- worker id is recorded;
- locked_at is recorded;
- lease_expires_at is recorded;
- PublicationAttempt STARTED is created.

Multiple workers therefore cannot claim the same row concurrently.

## Lease contract

Only the owning worker may renew or complete a claimed job.

Expired processing/publishing leases are recovered.

If attempts remain:
- status -> retry_wait;
- a deterministic retry time is scheduled;
- the active attempt becomes retrying.

If the final attempt has been consumed:
- job -> failed;
- RouteExecution -> failed.

## Retry contract

Base retry behavior is exponential:

```text
delay = min(base * 2^(attempt-1), cap)
```

When Telegram supplies RetryAfter/FloodWait:

```text
delay = max(normal_backoff, telegram_retry_after)
```

A Telegram wait is therefore never shortened by the local retry cap.

## Max attempts

Default max attempts: 5.

A route or destination can provide a bounded integer `max_attempts` override through settings.

After the final attempt, another retry request converts the job to failed rather than scheduling infinite retries.

## Checkpoint catch-up

Phase 6 integration tests exposed a valid out-of-order completion scenario:

1. a later event became queue-safe;
2. an older event was still blocking;
3. the later checkpoint advance correctly failed;
4. the older blocker then completed;
5. no later event emitted another checkpoint call.

The previous coordinator could remain one cursor behind even though every execution was now safe.

The coordinator now:
- never crosses the earliest blocking cursor;
- automatically advances to the highest durable safe cursor before that blocker;
- catches up through later already-safe events when an older blocker clears.

This preserves the anti-skip guarantee while removing ordering dependence.

## Runtime recovery

`QueueReliabilityRecoveryComponent` starts before Telegram ingestion.

It performs and periodically repeats:
1. expired lease recovery;
2. content recovery for `received/processing`;
3. dedup recovery for `ready_for_dedup`;
4. queue recovery for `ready_for_queue`.

All recovery operations are idempotent.

This covers failures after durable acceptance without replaying source history.

## Crash boundary

Phase 6 guarantees recovery for work that has completed the durable registration transaction.

A crash before that transaction commits is not acknowledged work and remains governed by the live-only ingestion contract.

## Migration

Revision:

`20261008_04_publish_queue_reliability.py`

Adds:
- `source_event_snapshots`;
- `route_publish_payloads`;
- V3 PublishJob columns/indexes/constraints;
- `manual_hold` job status.

### Downgrade

Phase 6 rows and schema additions are removed.

Any manual-hold jobs are mapped to queued.

The V2-owned PostgreSQL `job_status` enum keeps the now-inert `manual_hold` label after downgrade. This is intentional: enum labels in a baseline type are treated as additive/monotonic rather than rebuilding the production-owned enum.

CI verifies downgrade to base and re-upgrade through Phase 6.

## Verification

GitHub Actions Run `37707590801` completed successfully at:

`d58cac213f9f87afb0a0b3f611e4dbb998ebb9d8`

Passed:
- 39 focused V3 contract tests;
- 117 non-integration tests, 21 deselected;
- Ruff;
- compileall;
- PostgreSQL 16 V2 schema bootstrap;
- Alembic upgrade through `20261008_04`;
- Alembic downgrade to base;
- Alembic re-upgrade through `20261008_04`;
- 20 PostgreSQL V3 integration tests;
- V3 CLI;
- Docker build.

## Acceptance evidence

Verified in PostgreSQL:
- source-event snapshot persisted alongside accepted ingestion;
- snapshot normalization roundtrip preserves processing inputs;
- route payload is durable before ready-for-dedup;
- enqueue is idempotent;
- one job per RouteExecution;
- queued checkpoint durability;
- crash recovery from received;
- crash recovery from ready-for-dedup;
- crash recovery from ready-for-queue;
- out-of-order checkpoint catch-up;
- RetryAfter/FloodWait minimum;
- max attempt exhaustion;
- expired lease recovery;
- manual hold/release;
- V3 jobs are explicitly isolated from legacy V2 jobs.

## Explicit exclusions

Phase 6 does not:
- call Telegram Bot API;
- acquire/download media bytes for publishing;
- publish photos/videos/documents/audio/voice;
- publish albums;
- enforce Telegram caption/body limits;
- persist successful target message ids from real publishing;
- perform real Telegram E2E;
- change Railway production.

These are Phase 7+.

## Production safety

No V3 migration was applied to production.

No Railway branch, command, variable, volume or Telegram session was changed.

Production remains Runtime V2 on `main`.
