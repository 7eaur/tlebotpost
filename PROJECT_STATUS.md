# Project Status — Telegram Relay Rebuild

Date: 2026-10-08
Rebuild branch: `rebuild/v3-foundation-20261007`
Production baseline: `main@0990fb62b97c09a3fa44e41fe3c087bb5d3fd2cf`

## Current phase

Phase 4 — Content Processing

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
- `backend/migrations/README.md`
- `PROJECT_STATUS.md`

## Next phase

Phase 5 — Deduplication

Required work:
- typed fingerprint generation;
- exact Telegram/event identity handling;
- text fingerprint only when normalized text is non-empty;
- media fingerprint only when media exists;
- combined fingerprint only from meaningful components;
- destination/route scope and configured time window;
- concurrency-safe uniqueness semantics;
- deliberate duplicate vs unique-message reason codes;
- explicit consumption of `ready_for_dedup`;
- regression tests for the V2 empty-text/empty-media fingerprint defect;
- PostgreSQL concurrency/integration coverage.

Phase 5 must not add queue/publisher behavior beyond the minimum handoff contract. Publish-queue reliability remains Phase 6.
