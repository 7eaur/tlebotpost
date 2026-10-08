# Telegram Relay V3 — Operations Runbook

Date: 2026-10-09  
Scope: V3 pre-cutover operations

## 1. Safety rules

1. Never commit or paste Telegram session files, Bot tokens, API hashes, passwords or database credentials into Git/issues/logs.
2. Never run one Telegram user session concurrently in V2 and V3.
3. Do not apply V3 migrations to production until the migration/cutover gate explicitly authorizes it.
4. Diagnose by IDs/status/reason codes before reading raw provider errors.
5. Restore backups into an isolated environment first.

## 2. First checks

Owner Control Bot:

```text
/status
/metrics
```

Use `/status` for:
- Telegram connection;
- topology counts;
- pending/failed queue count;
- latest high-level error codes.

Use `/metrics` for queue and RouteExecution state distribution.

## 3. Diagnose one failed publish

Use:

```text
/job JOB_UUID
```

Inspect in order:
1. PublishJob status;
2. RouteExecution status/reason;
3. last error code;
4. attempt count vs max attempts;
5. latest attempt status/error code;
6. next retry time;
7. confirmed Telegram target message ids.

Do not copy message bodies into diagnostics.

### Important outcomes

`telegram_retry_after`
- provider asked the worker to wait;
- queue should move to retry_wait.

`telegram_forbidden`
- target permissions/bot membership/admin rights need operator review.

`telegram_bad_request`
- request shape or target state is invalid;
- do not loop retries blindly.

`source_media_unavailable`
- accepted source media can no longer be acquired.

`partial_publish`
- at least one target message was confirmed;
- V3 fails closed rather than replaying the logical post.

`publish_outcome_unknown`
- worker lost certainty after external publishing began;
- do not automatically replay; inspect target and confirmed ids manually.

## 4. Queue operations

Manual-held job:

```text
/release JOB_UUID
```

Topology change:

```text
/reload
```

A reload does not replay source history. It restores seen floors from SourceCheckpoint.

## 5. PostgreSQL backup

Use PostgreSQL-native logical backup tooling from a trusted operator environment.

Do not place a password directly in a shell command or documentation.

Recommended pattern:
1. obtain host/port/database/user through the secure provider/operator channel;
2. provide authentication through a protected `.pgpass` or equivalent secret mechanism;
3. run a custom-format dump:

```bash
pg_dump --format=custom --file=telegram-relay.backup --host="$PGHOST" --port="$PGPORT" --username="$PGUSER" "$PGDATABASE"
```

The application `DATABASE_URL` uses the SQLAlchemy `postgresql+asyncpg://` scheme and should not be pasted directly into PostgreSQL CLI examples.

Record separately:
- backup timestamp;
- source environment;
- schema/Alembic revision;
- application commit;
- encrypted backup storage location.

## 6. PostgreSQL restore validation

Never validate the first restore over production.

Create an isolated PostgreSQL database and restore:

```bash
pg_restore --clean --if-exists --no-owner --host="$PGHOST" --port="$PGPORT" --username="$PGUSER" --dbname="$PGDATABASE" telegram-relay.backup
```

Then verify:
- expected account/project/source/destination/route counts;
- SourceCheckpoint values;
- PublishJob state counts;
- Alembic current revision;
- V3 health command;
- no production Telegram session is mounted.

Only a separately approved migration/cutover procedure may use the restored data for a live V3 pilot.

## 7. Telegram user-session backup

Telegram session files are authentication material.

Rules:
1. stop the process owning the session before copying it;
2. ensure no second V2/V3 process uses the same session;
3. copy through a secure operator/storage channel;
4. restrict access to the smallest operator set;
5. never commit the file to Git;
6. never attach it to tickets/chat/documentation;
7. validate a restored copy only in the approved isolated environment.

For current production, the authorized session remains on the persistent Railway application volume. Phase 9 does not copy it.

## 8. Media staging

V3 media staging is temporary attempt data, not a backup source.

Do not back up staging directories as durable content.

The publisher removes attempt staging after completion and cleans stale managed directories at startup.

## 9. Logs and SystemEvents

Structured logs are designed to contain IDs/status/reasons rather than message bodies.

If a new code path wants to add operational event details:
- use primitive IDs/counts/status values;
- do not add tokens/session/content/caption/message text;
- use `record_event()`, which rejects unsafe detail keys/values.

## 10. Restart

Before an approved V3 restart:
1. note current commit;
2. inspect `/status` and `/metrics`;
3. allow normal graceful shutdown where possible;
4. restart one V3 process only;
5. confirm runtime-ready notification;
6. re-check queue/retry counts.

Phase 6 recovery will reconcile durable pre-queue work and expired safe leases.

Jobs whose external publishing outcome is unknown fail closed instead of being blindly replayed.

## 11. Production rollback/cutover

Not authorized by this runbook alone.

Until Phases 10–12 are complete:
- Railway production remains V2;
- V3 stays isolated;
- production session remains where it is;
- production database is not migrated by V3 development commands.

Formal migration, pilot and cutover documents supersede this section when those phases are verified.
