# V3 database migrations

Alembic is the migration mechanism for the V3 rebuild.

## Current baseline

Production currently uses the V2 PostgreSQL schema originally bootstrapped by:

`backend/db/schema.sql`

V3 migrations are additive to that reference schema and are not run against production during rebuild phases.

## Revisions

### `20261007_01_route_executions.py`

Adds:
- `route_execution_status`;
- `route_executions`;
- `source_checkpoints.last_committed_message_id`.

The committed cursor is initially backfilled from the current legacy cursor.

### `20261008_02_content_processing_state.py`

Adds the Phase 4 handoff enum value:

- `ready_for_dedup`.

This state is intentionally not checkpoint-safe and not final.

Downgrade behavior:
1. maps any `ready_for_dedup` rows back to `processing`;
2. recreates the previous PostgreSQL enum without the Phase 4 value;
3. restores the RouteExecution status default.

The full upgrade/downgrade/re-upgrade roundtrip is verified in CI against PostgreSQL 16.

### `20261008_03_deduplication.py`

Adds:
- `ready_for_queue` to `route_execution_status`;
- `route_fingerprints` for V3 typed dedup observations;
- lookup index across account/scope/type/fingerprint/time.

The table is RouteExecution-linked and intentionally separate from V2 `content_fingerprints`.

The migration downgrade:
1. drops the V3 route-fingerprint table;
2. maps `ready_for_queue` back to `ready_for_dedup`;
3. recreates the previous PostgreSQL enum.

The complete upgrade/downgrade/re-upgrade path through `20261008_03` is verified in CI.

## Safety rules

- do not auto-run legacy SQLite import from application startup;
- do not run experimental migrations against production;
- test upgrade and downgrade on a disposable PostgreSQL database first;
- production migration happens only during the controlled migration/cutover phases;
- `backend/db/schema.sql` remains the V2 bootstrap reference, not a V3 migration history.

## Commands

From `backend/`:

```bash
alembic history
alembic current
alembic upgrade head
alembic downgrade base
```

`DATABASE_URL` must use the `postgresql+asyncpg://` SQLAlchemy scheme.

## CI verification

The V3 workflow starts PostgreSQL 16, loads the V2 reference schema, runs:

```bash
alembic upgrade head
alembic current
alembic downgrade base
alembic upgrade head
```

and then executes the V3 PostgreSQL domain, ingestion, and content-processing integration suite.
