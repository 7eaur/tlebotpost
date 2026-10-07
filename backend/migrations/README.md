# V3 database migrations

Alembic is the migration mechanism for the V3 rebuild.

## Current baseline

Production currently uses the V2 PostgreSQL schema originally bootstrapped by:

`backend/db/schema.sql`

The first V3 revision is:

`20261007_01_route_executions.py`

It is intentionally additive and assumes the V2 schema already exists.

It adds:

- `route_execution_status`;
- `route_executions`;
- `source_checkpoints.last_committed_message_id`.

The committed cursor is backfilled from the current legacy cursor.

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
alembic downgrade base
alembic upgrade head
```

and then executes the V3 PostgreSQL integration suite.
