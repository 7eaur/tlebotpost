# Project Status — Telegram Relay Rebuild

Date: 2026-10-08
Rebuild branch: `rebuild/v3-foundation-20261007`
Production baseline: `main@0990fb62b97c09a3fa44e41fe3c087bb5d3fd2cf`

## Current phase

Phase 5 — Deduplication

Status: COMPLETE / VERIFIED IN CI  
External real-Telegram V3 pilot: DEFERRED UNTIL AN ISOLATED V3 SESSION IS AVAILABLE

## Production state

- Railway production remains on `main`.
- No V3 rebuild commit has changed the live service.
- Runtime V2 remains the production reference.
- The current production Telegram session/volume was deliberately not moved or shared with V3.
- The known V2 false-deduplication defect remains isolated from the rebuild path.
- V3 Phase 4 was verified only in CI/disposable PostgreSQL; no production migration was run.

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
- `backend/migrations/README.md`
- `PROJECT_STATUS.md`

## Next phase

Phase 6 — Publish Queue and Reliability

Required work:
- durable queue handoff from `ready_for_queue`;
- route-specific publish payload durability;
- idempotent enqueue;
- claim/lease semantics;
- retries/backoff;
- FloodWait-aware retry scheduling;
- max attempts;
- stuck-job recovery;
- restart/crash recovery tests;
- checkpoint transition only after durable queue representation.

Phase 6 must not perform the full Telegram media publishing lifecycle; that remains Phase 7.
