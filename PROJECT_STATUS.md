# Project Status — Telegram Relay Rebuild

Date: 2026-10-08
Rebuild branch: `rebuild/v3-foundation-20261007`
Production baseline: `main@0990fb62b97c09a3fa44e41fe3c087bb5d3fd2cf`

## Current phase

Phase 6 — Publish Queue and Reliability

Status: COMPLETE / VERIFIED IN CI  
External real-Telegram V3 pilot: DEFERRED UNTIL AN ISOLATED V3 SESSION IS AVAILABLE

## Production state

- Railway production remains on `main`.
- No V3 rebuild commit has changed the live service.
- Runtime V2 remains the production reference.
- The current production Telegram session/volume was deliberately not moved or shared with V3.
- The known V2 false-deduplication defect remains isolated from the rebuild path.
- V3 Phases 4-6 were verified only in CI/disposable PostgreSQL; no production migration was run.

## Completed rebuild phases

### Phase 0 — Audit and contracts

Completed:
- current-system audit;
- product behavior reconstruction;
- target architecture;
- rebuild master plan;
- confirmed V2 duplicate-fingerprint defect.

### Phase 1 — Runtime foundation

Completed and verified:
- canonical `python -m app.v3` runtime;
- centralized V3 settings;
- lifecycle state machine;
- PostgreSQL readiness;
- healthcheck;
- Alembic;
- Docker/Compose V3 path;
- CI gates.

### Phase 2 — Core domain and route execution

Completed and verified:
- reuse of Account/Project/TelegramAccount/Source/Destination/SourceRoute;
- durable `RouteExecution`;
- route-event idempotency;
- explicit execution-state machine;
- `last_committed_message_id`;
- checkpoint anti-skip rules;
- additive migration `20261007_01`;
- PostgreSQL integration.

### Phase 3 — Telegram ingestion

Completed and verified:
- `TelegramUserAdapter` / `TelethonUserAdapter`;
- typed `SourceEvent`;
- album aggregation;
- live-only startup/reconnect baseline;
- durable route fan-out before seen-cursor advancement;
- Telegram-account operational state;
- reconnect lifecycle;
- active source/project/destination/route hierarchy;
- PostgreSQL integration.

## Phase 4 delivered

### Route-specific processing boundary

The Phase 3 `on_registration` handoff now invokes a dedicated V3 content-processing coordinator after durable RouteExecution registration.

Each route is processed independently. Processing does not live inside the Telethon adapter and does not change Telegram ingestion semantics.

### Route policy resolution

Added account-scoped resolution for:
- `FilterProfile`;
- `TransformProfile`;
- route-level `BrandingProfile`;
- destination branding fallback.

Route branding overrides destination branding when both are configured.

### Normalized content contract

Added typed contracts for:
- normalized text;
- rendered/publish text;
- normalized media descriptors;
- content type;
- processing decision;
- deterministic reason code.

The normalized text is deliberately kept separate from branded/rendered text so future deduplication does not accidentally fingerprint destination branding.

### Arabic/text-safe normalization

Implemented deterministic handling for:
- Arabic text;
- CRLF/LF normalization;
- whitespace trimming;
- bounded blank lines;
- optional line-break flattening;
- emoji preservation by default;
- optional emoji removal without broad Arabic-codepoint stripping.

### Rights and URL handling

Implemented:
- trailing source/right/credit block removal;
- Telegram-link removal;
- general URL removal;
- independent Telegram/general URL switches through filter custom rules.

Rights removal is suffix-oriented instead of deleting arbitrary matching content in the body.

### Media and album classification

Implemented normalized media descriptors for:
- photo;
- video;
- document;
- audio;
- voice;
- unknown media.

Albums remain one logical content unit, preserve source order, and expose stable media identities without downloading/staging files in Phase 4.

### Filters

Implemented deterministic route filtering with reason codes:
- `missing_include_keyword`;
- `matched_exclude_keyword`;
- `media_type_not_allowed`;
- `empty_content`.

### Branding

Branding is rendered only after normalization/filtering.

Supported:
- footer;
- link;
- separator;
- route override;
- destination fallback.

### RouteExecution handoff state

Added `ready_for_dedup` through additive migration `20261008_02`.

Processing flow is now:

```text
received
  -> processing
      -> filtered
      -> ready_for_dedup
      -> failed
```

`ready_for_dedup` is intentionally:
- not final;
- not checkpoint-safe.

This prevents the source committed checkpoint from advancing before Phase 5+ has recorded a durable downstream outcome.

Filtered/failed outcomes continue to participate in checkpoint advancement under the existing Phase 2 coordinator.

### Runtime integration

`RuntimeV3.from_settings()` wires `ContentProcessingCoordinator.process_registration` into the existing Phase 3 registration callback.

No production/Railway command was changed.

## Phase 4 persistence boundary

Phase 4 persists processing state on `RouteExecution`, but it does not yet persist a route-specific transformed content payload or staged media.

The processed result is passed through the application boundary for the next stage. Durable dedup/queue/media handoff and crash recovery remain Phase 5+ responsibilities.

This is acceptable for the isolated rebuild phase, but V3 must not cut over to production until downstream durability is completed and exercised end-to-end.

## Verification evidence

Verified code head:

`621ba5e13f137bee0ac6c8b57674e2129753b31c`

GitHub Actions:

- Run ID: `37694872101`
- Conclusion: `success`

Passed:

- focused V3 phase-contract tests: 24 passed;
- full non-integration suite: 102 passed, 10 deselected;
- Ruff: all checks passed;
- compileall;
- PostgreSQL 16 V2-reference schema bootstrap;
- Alembic upgrade to `20261008_02`;
- Alembic downgrade to base;
- Alembic re-upgrade to `20261008_02`;
- PostgreSQL V3 domain + ingestion + content integration: 9 passed;
- V3 CLI;
- Docker build.

Phase 4 integration evidence covers:
- route-profile isolation;
- route branding over destination branding;
- destination branding fallback;
- route-specific emoji policy;
- independent normalization outcomes;
- `ready_for_dedup` persistence;
- all-filtered fan-out allowing committed checkpoint advancement.

## Phase 5 delivered

### Typed fingerprint contract

V3 now emits explicit fingerprint types:
- `telegram_identity`;
- `text`;
- `media`;
- `combined`.

The V2 empty-component defect is removed by contract:
- no text fingerprint is created when normalized text is empty;
- no media fingerprint is created when the media set is empty;
- combined fingerprints are created only from meaningful enabled content signals.

### Scoped deduplication

Default V3 content-dedup scope is `route`.

This prevents accidental cross-source comparison.

Cross-source deduplication is enabled only when a profile explicitly sets:

```json
{"scope": "destination"}
```

Route-level DeduplicationProfile overrides destination fallback.

### Time windows

Content fingerprints are matched only inside the current profile `window_seconds`.

Telegram identity remains an exact identity signal and is not reduced to a content-window comparison.

### Concurrency protection

Dedup decisions are protected with PostgreSQL transaction-scoped advisory locks derived from:
- account;
- dedup scope;
- fingerprint type;
- fingerprint value.

Matching checks and fingerprint recording occur in the same database transaction.

The PostgreSQL integration suite verifies that two matching messages processed concurrently produce exactly one unique winner and one duplicate outcome.

### Durable fingerprint observations

Added `route_fingerprints`, linked to `RouteExecution`.

It records:
- account;
- route execution;
- scope key;
- fingerprint type;
- fingerprint value;
- observation timestamp.

This table is separate from the V2 `content_fingerprints` table because V2 storage is ContentItem-bound and its permanent uniqueness constraint does not model the V3 time-window contract correctly.

### RouteExecution handoff

Migration `20261008_03` adds:

`ready_for_queue`

Phase 5 state flow:

```text
ready_for_dedup
  -> duplicate
  -> ready_for_queue
  -> failed
```

`ready_for_queue` is intentionally:
- not final;
- not checkpoint-safe.

A unique item therefore cannot advance `last_committed_message_id` until Phase 6 has persisted the publish-queue outcome.

A duplicate is final/checkpoint-safe and uses the existing checkpoint coordinator.

### Reason codes

Unique / bypass:
- `dedup_unique`;
- `dedup_disabled`;
- `dedup_no_signals`.

Duplicate:
- `duplicate_exact_identity`;
- `duplicate_matching_text`;
- `duplicate_matching_media`;
- `duplicate_matching_combined`.

Failure:
- `deduplication_error`.

### Runtime integration

`RuntimeV3.from_settings()` now wires:

```text
ContentProcessingCoordinator
        -> DeduplicationCoordinator
```

No queue/publisher behavior is introduced by Phase 5.

## Phase 5 verification evidence

Verified code head:

`e93e1b446a1f9a88bef7cda082ffaf25c52d6e5e`

GitHub Actions:

- Run ID: `37699795962`
- Conclusion: `success`

Passed:
- focused V3 phase-contract tests: 31 passed;
- full non-integration suite: 109 passed, 16 deselected;
- Ruff: all checks passed;
- compileall;
- PostgreSQL 16 V2-reference schema bootstrap;
- Alembic upgrade through `20261008_03`;
- Alembic downgrade to base;
- Alembic re-upgrade through `20261008_03`;
- PostgreSQL V3 domain + ingestion + content + dedup integration: 15 passed;
- V3 CLI;
- Docker build.

Phase 5 regression/integration evidence covers:
- distinct text-only messages are not false duplicates from empty media;
- media-only messages are not false duplicates from empty text;
- real matching text is rejected with typed reason;
- real matching media is rejected with typed reason;
- content duplicate windows expire correctly;
- destination scope enables explicit cross-source deduplication;
- concurrent matching messages produce one unique winner and one duplicate.

## Phase 5 boundary

Phase 5 does not:
- create PublishJob rows;
- claim jobs;
- retry/failover publishing;
- stage Telegram media;
- call Bot API;
- alter Railway production.

Processed publish payload durability/recovery must be completed as part of the Phase 6 queue handoff before V3 is eligible for production cutover.

## Phase 6 delivered

### Durable source-event snapshot

The ingestion registration transaction now also persists `source_event_snapshots`.

The snapshot contains only the processing inputs needed for deterministic recovery:
- source/event identity;
- ordered Telegram message ids;
- grouped id;
- chat id;
- raw text/caption;
- media type/identity metadata;
- received timestamp.

This closes the crash window where `last_seen_message_id` had advanced after durable RouteExecution registration but content processing had not completed.

Recovery does not replay Telegram history. It resumes from the durable snapshot already accepted by V3.

### Durable route publish payload

Added `route_publish_payloads`, one payload per RouteExecution.

It persists:
- normalized text;
- rendered/branding text;
- content type;
- ordered media descriptors.

The payload is written in the same database transaction that moves the execution to `ready_for_dedup`.

This makes Phase 4 -> Phase 5 -> Phase 6 recoverable after restart without keeping transformed content only in process memory.

### V3 queue handoff

The existing `publish_jobs` table is extended rather than duplicated.

V3 jobs add:
- `route_execution_id`;
- `route_payload_id`;
- `max_attempts`;
- `lease_expires_at`.

`content_item_id` is nullable so V3 route-specific payloads do not have to be forced into the V2 ContentItem contract.

One unique PublishJob is allowed per RouteExecution.

A RouteExecution moves from `ready_for_queue` to `queued` only after the durable job exists. The source checkpoint may advance only after this durable representation is present.

### Scheduling and manual mode

Publishing mode precedence:
1. route publishing mode;
2. destination publishing mode.

Supported Phase 6 queue states:
- direct -> queued immediately;
- scheduled -> queued for the resolved schedule;
- manual -> `manual_hold` until explicit release.

Manual jobs are durable and are not claimable by workers before release.

### Claim and lease

V3 workers claim only V3 jobs carrying both:
- `route_execution_id`;
- `route_payload_id`.

This prevents V3 workers from claiming legacy V2 queue rows.

Due jobs are claimed with PostgreSQL `FOR UPDATE SKIP LOCKED`.

A claim records:
- worker id;
- locked timestamp;
- lease expiry;
- incremented attempt count;
- PublicationAttempt STARTED row.

Leases can be renewed by the owning worker.

### Retry / FloodWait contract

Retries use deterministic exponential backoff with a configured cap.

A Telegram RetryAfter/FloodWait value is treated as a minimum retry delay and is never shortened by the normal backoff cap.

Jobs stop retrying when `max_attempts` is exhausted and move to failed.

### Stale-work recovery

Expired worker leases are recovered:
- to `retry_wait` when attempts remain;
- to `failed` when max attempts are exhausted.

Runtime V3 now starts a reliability recovery component before Telegram ingestion.

It periodically recovers:
1. expired queue leases;
2. `received/processing` RouteExecutions from source-event snapshots;
3. `ready_for_dedup` executions from durable route payloads;
4. `ready_for_queue` executions into idempotent PublishJobs.

### Checkpoint catch-up

Phase 6 testing exposed an ordering edge case: a later cursor could become queue-safe before an older blocker cleared.

`SourceCheckpointCoordinator` now catches up automatically to the highest fully safe cursor before the next blocking execution.

It still never jumps over unfinished work.

### Migration

Added `20261008_04_publish_queue_reliability.py`.

It adds:
- `source_event_snapshots`;
- `route_publish_payloads`;
- V3 PublishJob references/reliability fields;
- `manual_hold` job status;
- V3 due-job index.

Because `job_status` belongs to the V2 reference schema, the Phase 6 downgrade treats the added PostgreSQL enum label as monotonic: V3 rows/tables/columns are removed and any manual jobs are mapped to `queued`, while the inert enum label may remain.

This avoids unsafe reconstruction of a V2-owned PostgreSQL enum.

## Phase 6 verification evidence

Verified code head:

`d58cac213f9f87afb0a0b3f611e4dbb998ebb9d8`

GitHub Actions:

- Run ID: `37707590801`
- Conclusion: `success`

Passed:
- focused V3 contract tests: 39 passed;
- full non-integration suite: 117 passed, 21 deselected;
- Ruff: all checks passed;
- compileall;
- PostgreSQL 16 V2-reference schema bootstrap;
- Alembic upgrade through `20261008_04`;
- Alembic downgrade to base;
- Alembic re-upgrade through `20261008_04`;
- PostgreSQL V3 integration through queue reliability: 20 passed;
- V3 CLI;
- Docker build.

Phase 6 integration evidence covers:
- durable route payload + idempotent one-job handoff;
- source-event snapshot recovery;
- crash recovery from `received`;
- crash recovery from `ready_for_dedup`;
- crash recovery from `ready_for_queue`;
- checkpoint catch-up after out-of-order completion;
- RetryAfter-aware scheduling;
- max-attempt exhaustion;
- expired-lease recovery;
- manual hold/release;
- V3-only job claiming.

## Phase 6 boundary

Phase 6 does not:
- call the Telegram Bot API;
- stage/download Telegram media bytes;
- publish text/media/albums;
- map Telegram publishing errors;
- mark successful jobs published from a real target;
- alter Railway production.

Those are Phase 7 responsibilities.

## External Telegram pilot

Not executed for V3 yet.

The live authorized session is stored on the production V2 Railway volume. The production session was not copied, moved, mounted into V3, or run concurrently.

The external V3 pilot remains a controlled future validation step once an isolated V3 Telegram session exists. This is explicitly documented and is not being presented as completed.

## Source of truth

- `docs/REBUILD_MASTER_PLAN.md`
- `docs/v3-phase1-runtime-foundation.md`
- `docs/v3-phase2-core-domain.md`
- `docs/v3-phase3-telegram-ingestion.md`
- `docs/v3-phase4-content-processing.md`
- `docs/v3-phase5-deduplication.md`
- `docs/v3-phase6-publish-queue-reliability.md`
- `backend/migrations/README.md`
- `PROJECT_STATUS.md`

## Next phase

Phase 7 — Publisher and Media Lifecycle

Required work:
- V3 worker loop over leased PublishJobs;
- durable route-payload loading;
- Bot API publishing for text;
- media acquisition/staging through the authorized user session;
- photo/video/document/audio/voice publishing;
- album semantics as one logical job;
- caption/text limits and deterministic fallback;
- Telegram error classification;
- queue retry/failure integration;
- published-message durability;
- media cleanup/expiry;
- isolated real-Telegram sandbox evidence for supported types.

Production Railway remains unchanged until the formal pilot/cutover phases.
