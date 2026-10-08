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

### `20261008_04_publish_queue_reliability.py`

Adds:
- `source_event_snapshots` for crash-safe recovery after durable Telegram acceptance;
- `route_publish_payloads` for route-specific transformed publish payloads;
- nullable V3 path for `publish_jobs.content_item_id`;
- `route_execution_id` and `route_payload_id` on PublishJob;
- `max_attempts` and `lease_expires_at`;
- V3 due-job lookup index;
- additive `manual_hold` value in the V2-owned `job_status` enum.

Downgrade removes all Phase 6 tables, columns, indexes, constraints and V3 queue rows and maps manual-hold rows to queued.

The `manual_hold` PostgreSQL enum label is deliberately monotonic on downgrade because `job_status` belongs to the V2 reference schema rather than to V3 Alembic. Reconstructing that V2-owned enum proved unsafe; leaving an unused additive label is backward-compatible.

The complete upgrade/downgrade/re-upgrade path through `20261008_04` is verified in PostgreSQL 16 CI.

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

and then executes the V3 PostgreSQL domain, ingestion, content-processing, deduplication, and publish-queue reliability integration suite.
