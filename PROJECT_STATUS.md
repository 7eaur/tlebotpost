# Project Status — Telegram Relay Rebuild

Date: 2026-10-07
Rebuild branch: `rebuild/v3-foundation-20261007`
Production baseline: `main@0990fb62b97c09a3fa44e41fe3c087bb5d3fd2cf`

## Current phase

Phase 2 — Core Domain and Route Execution

Status: COMPLETE / VERIFIED

## Production state

- Railway production remains on `main`.
- No V3 rebuild commit has changed production behavior.
- Runtime V2 remains the live production reference.
- The known V2 false-duplicate defect remains documented and intentionally isolated from the V3 rebuild.

## Phase 1 baseline

Completed and verified:
- canonical V3 runtime entrypoint: `python -m app.v3`;
- centralized settings;
- lifecycle state machine;
- PostgreSQL readiness;
- V3 healthcheck;
- Alembic migration runner;
- Docker/Compose V3 path;
- CI verification.

## Phase 2 delivered

### Domain reuse instead of duplication

V3 keeps the useful existing PostgreSQL entities:

- Account
- Project
- TelegramAccount
- Source
- Destination
- SourceRoute

It does not create duplicate V3 copies of them.

### New durable RouteExecution model

Added `route_executions` as the durable unit of one source event being processed for one destination route.

Recorded identity includes:
- account;
- source;
- route;
- destination;
- stable event key;
- cursor message id;
- Telegram message/group identity;
- processing status;
- reason code;
- optional content/publish-job references;
- terminal timestamp.

Idempotency key:

`(route_id, event_key)`

### Event identity

Single message:

`message:<message_id>`

Album/group:

`group:<grouped_id>`

### Execution states

- received
- processing
- filtered
- duplicate
- queued
- published
- failed
- cancelled

Checkpoint-safe:
- filtered
- duplicate
- queued
- published
- failed
- cancelled

Blocking:
- received
- processing

Final:
- filtered
- duplicate
- published
- failed
- cancelled

Queued is intentionally checkpoint-safe but not final because a durable publish job can continue after restart.

### Checkpoint contract

Added:

`source_checkpoints.last_committed_message_id`

V3 checkpoint advancement now requires:

1. route executions exist for the candidate cursor;
2. every route execution at that cursor is checkpoint-safe;
3. no older execution at or below the candidate cursor remains received/processing;
4. the source belongs to the current account.

This prevents a newer event from skipping unfinished older work.

### Operational hierarchy

New route executions require all of the following to be active:

- Project
- Source
- Destination
- SourceRoute

Pausing a project therefore stops new route work below it.

### Deletion safety

RouteExecution references:
- Source
- SourceRoute
- Destination

with `ON DELETE RESTRICT`.

Pending execution evidence cannot be silently erased by deleting operational configuration. Normal lifecycle should use pause/archive instead of destructive delete.

### First V3 migration

Revision:

`20261007_01`

Adds:
- PostgreSQL enum `route_execution_status`;
- `route_executions`;
- `source_checkpoints.last_committed_message_id`;
- committed-cursor backfill from legacy cursor;
- operational indexes;
- updated-at trigger.

The migration is additive over the current V2 PostgreSQL schema.

### CI improvements

The V3 workflow now:
- runs PostgreSQL 16;
- loads the V2 reference schema;
- runs Alembic upgrade;
- runs Alembic downgrade to base;
- upgrades again;
- runs real PostgreSQL V3 integration tests;
- runs Docker build;
- uses concurrency cancellation so stale rebuild runs do not waste CI capacity.

## Verification evidence

Verified GitHub Actions run:

- Run ID: `37687370042`
- Head: `40637bd582737b92842dbb997072193b7cfeacf2`
- Conclusion: `success`

Passed gates:

- V3 focused tests: 15 passed.
- Full non-integration suite: 88 passed, 6 integration tests deselected.
- Ruff: passed.
- compileall: passed.
- PostgreSQL 16 bootstrap: passed.
- Alembic upgrade: passed.
- Alembic downgrade: passed.
- Alembic re-upgrade: passed.
- V3 PostgreSQL route-execution integration: 5 passed.
- V3 CLI: passed.
- Docker image build: passed.

PostgreSQL integration verifies:
- source fan-out to multiple routes;
- idempotent event registration;
- unfinished route blocking checkpoint;
- queued route being checkpoint-safe;
- older unfinished event blocking newer cursor;
- account isolation;
- paused project suppressing new execution;
- final execution cannot return to queue.

## Source of truth

- `docs/REBUILD_MASTER_PLAN.md`
- `docs/v3-phase1-runtime-foundation.md`
- `docs/v3-phase2-core-domain.md`
- `backend/migrations/README.md`
- `PROJECT_STATUS.md`

Production `main` remains unchanged until controlled V3 cutover.

## Next phase

Phase 3 — Telegram Ingestion

Planned work:
- V3 Telegram user-session component;
- live-only baseline using committed cursor semantics;
- source subscription/resubscription;
- reconnect lifecycle;
- typed source events;
- album/group collector;
- handoff from Telegram event -> RouteExecution registration;
- source-level serialization so events cannot race past checkpoints;
- integration tests with fake Telegram adapter;
- pilot-safe real Telegram ingestion validation without production publishing.

Phase 3 must not yet introduce content transformation, deduplication, or target publishing. Those remain later gates.
