# V3 Phase 10 — Fresh Bootstrap and Runtime Carryover

Date: 2026-10-10  
Branch: `rebuild/v3-foundation-20261007`

## Decision

V1/V2 application data is not migrated.

V3 starts from a clean PostgreSQL application state. Old topology, content, queue history, attempts and publication history are intentionally excluded.

## What is preserved because V3 actually needs it

Runtime continuity requires:
- Telegram API application credentials;
- the publishing/control bot credential;
- the owner Telegram identifier;
- a PostgreSQL connection for the clean V3 database;
- the persistent application volume used for Telegram session state;
- the authorized Telegram user-session file at final controlled handoff;
- Railway service structure needed to build/run the backend.

The session is authentication state, not application-history migration.

## What is created fresh

The bootstrap creates only:
- one Account;
- one active Project;
- one TelegramAccount record.

It returns their generated identifiers for runtime configuration.

Sources, destinations and routes remain empty and are configured later through Control Bot V3.

## Database bootstrap

Added:

`python scripts/bootstrap_v3.py`

The command is idempotent.

On a truly empty PostgreSQL database it:
1. installs the repository reference schema once;
2. upgrades Alembic through the current V3 head;
3. creates the minimum V3 identity rows;
4. emits only non-secret generated identifiers/status metadata.

It does not invoke the legacy importer.

## CI acceptance

GitHub Actions Run `38063901338` passed at code head:

`5c5552debb31a004b32ffaa07f520fbd9037d383`

Verified:
- focused V3 tests: 63 passed;
- full non-integration suite: 141 passed, 31 deselected;
- Ruff: all checks passed;
- compileall;
- a second completely empty PostgreSQL database was created;
- fresh bootstrap was run twice to prove idempotency;
- exactly one Account, Project and TelegramAccount remained;
- Sources, Destinations and SourceRoutes remained zero;
- normal migration roundtrip passed;
- PostgreSQL integration: 30 passed;
- V3 CLI passed;
- Docker build passed.

## Railway facts retained for cutover

Current service structure already provides:
- repository backend root;
- Dockerfile build;
- persistent `/app/data` volume;
- PostgreSQL connectivity;
- existing runtime credential variables.

The current production start command is V2-specific and still invokes the legacy importer. It must not be reused for V3.

V3 cutover will use the repository-defined V3 entrypoint from the Dockerfile.

## Session handoff rule

The production Telegram session must never be active in V2 and V3 at the same time.

Before final handoff:
1. stop V2 ownership of the session;
2. preserve the persistent session file;
3. point V3 at the approved session path;
4. start exactly one V3 runtime;
5. validate connection before enabling normal topology.

The isolated real-Telegram pilot still requires a separate authorized session.

## Explicitly not carried forward

Not carried forward:
- legacy runtime identifiers;
- old source/destination/route rows;
- historical content;
- queue/publication history;
- old filter/profile rows;
- automatic legacy SQLite import;
- old runtime start command.

## Production safety

Phase 10 changed repository code and CI only.

Railway production remains on V2. No production database reset, session move or deployment was performed.
