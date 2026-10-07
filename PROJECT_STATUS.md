# Project Status — Telegram Relay Rebuild

Date: 2026-10-07
Rebuild branch: `rebuild/v3-foundation-20261007`
Production baseline: `main@0990fb62b97c09a3fa44e41fe3c087bb5d3fd2cf`

## Current phase

Phase 1 — Runtime Foundation

Status: COMPLETE / VERIFIED

## Production state

- Railway production remains on `main`.
- No production behavior was changed by V3 rebuild work.
- Runtime V2 remains the live production reference.
- The confirmed V2 false-duplicate defect remains documented and intentionally unpatched while V3 is rebuilt against explicit contracts.

## Phase 0 result

Completed:
- production/reference freeze;
- repository and runtime audit;
- product behavior reconstruction;
- target architecture;
- rebuild phases and acceptance gates;
- confirmed deduplication design defect;
- master plan in `docs/REBUILD_MASTER_PLAN.md`.

## Phase 1 delivered

### Canonical V3 runtime foundation
- `backend/app/v3/config.py`: centralized process configuration.
- `backend/app/v3/runtime.py`: lifecycle and readiness state machine.
- `backend/app/v3/__main__.py`: canonical V3 command: `python -m app.v3`.
- `backend/app/v3/health.py`: PostgreSQL readiness health check.

### Lifecycle guarantees
- PostgreSQL must be healthy before V3 becomes ready.
- components start in declared order.
- components stop in reverse order.
- partial startup failure rolls back components already started.
- runtime exposes explicit stopped/starting/ready/stopping/failed states.
- Telegram credentials are an optional grouped dependency until the ingestion phase is connected.

### Migration foundation
- Alembic added as the V3 migration mechanism.
- `backend/alembic.ini`.
- `backend/migrations/env.py`.
- `backend/migrations/script.py.mako`.
- Phase 1 deliberately creates no production schema revision; the first V3 domain revision belongs to Phase 2.
- automatic legacy import is not part of the V3 runtime foundation.

### Container/runtime alignment
On the rebuild branch:
- Dockerfile default command is V3.
- Docker healthcheck uses V3 health.
- optional Compose profile `runtime-v3` uses the same `python -m app.v3` entrypoint.
- V2 remains explicitly available during controlled migration.
- production Railway has not been switched.

## Verification evidence

Verified GitHub Actions run:

- Run ID: `37686224476`
- Head: `98fb09c8091317fd0d1c56c09d0a402ce032e9fc`
- Conclusion: `success`

Passed gates:
- V3 foundation tests: 6 passed.
- Full non-integration suite: 79 passed, 1 integration test deselected.
- Ruff: passed.
- compileall: passed.
- Alembic runner/history: passed.
- V3 CLI import/help: passed.
- Docker image build: passed.

## Source of truth

- `docs/REBUILD_MASTER_PLAN.md`
- `docs/v3-phase1-runtime-foundation.md`
- `PROJECT_STATUS.md`
- `main` remains the production baseline until controlled V3 cutover.

## Next phase

Phase 2 — Core Domain and Route Execution

Planned work:
- normalize V3 domain boundaries for Account, Project, TelegramAccount, Source, Destination and SourceRoute;
- define route execution as the durable unit of source-to-destination processing;
- define source checkpoint advancement around durable route outcomes;
- create the first V3 Alembic schema revision;
- add domain/service validation and account isolation;
- add PostgreSQL integration tests for the new schema and route-execution contracts.

Phase 2 must not connect production Telegram publishing yet. Production remains unchanged until later pilot/cutover gates.
