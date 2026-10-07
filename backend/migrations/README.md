# V3 database migrations

Alembic is the migration mechanism for the V3 rebuild.

Phase 1 establishes the migration runner only. The current V2 PostgreSQL schema remains the production baseline and is not silently stamped or modified by V3.

The first V3 schema revision is created in Phase 2 together with the normalized domain model. Until that revision exists:

- do not run V3 migrations against production;
- do not treat backend/db/schema.sql as a V3 migration;
- do not auto-run legacy import from application startup;
- use a separate test database for migration development.

Commands from backend/:

    alembic history
    alembic current
    alembic upgrade head

DATABASE_URL must use postgresql+asyncpg://.
