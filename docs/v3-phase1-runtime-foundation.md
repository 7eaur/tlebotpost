# V3 Phase 1 — Runtime Foundation

Branch: rebuild/v3-foundation-20261007

## Goal

Create a single, explicit V3 process foundation before rebuilding Telegram ingestion or business behavior.

## Added

- app/v3/config.py — centralized process configuration.
- app/v3/runtime.py — lifecycle/state orchestration.
- app/v3/__main__.py — canonical command: python -m app.v3.
- app/v3/health.py — PostgreSQL readiness health check.
- Alembic migration runner under backend/migrations.
- V3 foundation unit tests.
- GitHub Actions verification workflow.
- Docker image default command/healthcheck aligned to V3 on the rebuild branch.
- optional docker-compose runtime-v3 profile while runtime-v2 remains available during the transition.

## Deliberate boundaries

Phase 1 does not:
- connect V3 to Telegram;
- change production Railway;
- migrate production schema;
- fix deduplication inside V2;
- delete V1 or V2.

The migration runner is established now. The first V3 schema revision belongs to Phase 2, where the domain model is normalized. Running V3 migrations against production before Phase 2 is prohibited.

## Acceptance gates

1. V3 settings validate PostgreSQL and optionally Telegram as one credential group.
2. Runtime refuses readiness when PostgreSQL is unavailable.
3. Components start in order and stop in reverse.
4. Partial startup failure rolls back already-started components.
5. python -m app.v3 is the canonical V3 entrypoint.
6. Docker image builds with the V3 command and healthcheck.
7. Alembic runner imports successfully.
8. V3 tests, full non-integration tests, Ruff and compileall pass in CI.

Production stays on main until later cutover phases.
