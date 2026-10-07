# V3 Phase 2 — Core Domain and Route Execution

Branch: `rebuild/v3-foundation-20261007`

## Goal

Make source-to-destination work a durable, explicit domain concept before Telegram ingestion is rebuilt.

The existing V2 entities `Account`, `Project`, `TelegramAccount`, `Source`, `Destination` and `SourceRoute` are retained as the data foundation. V3 does not create duplicate copies of those tables.

## New durable concept: RouteExecution

One incoming Telegram source event fans out into one `RouteExecution` per active destination route.

A route execution records:

- account;
- source;
- route;
- destination;
- stable event key;
- source cursor message id;
- Telegram message/group identity;
- processing state;
- reason code;
- optional content/publish-job references;
- terminal timestamp.

The unique identity is:

`(route_id, event_key)`

This makes event registration idempotent per route.

## Event identity

Single message:

`message:<telegram_message_id>`

Album/group:

`group:<telegram_grouped_id>`

The album cursor is expected to use the highest source message id in the assembled group so checkpoint movement remains monotonic.

## Execution states

- `received`
- `processing`
- `filtered`
- `duplicate`
- `queued`
- `published`
- `failed`
- `cancelled`

### Checkpoint-safe states

A source checkpoint may move past an execution in:

- filtered
- duplicate
- queued
- published
- failed
- cancelled

`queued` is deliberately checkpoint-safe because the work is durably persisted for later publishing.

`received` and `processing` block the checkpoint.

### Final states

- filtered
- duplicate
- published
- failed
- cancelled

Queued is not final because the publisher can later transition it to published/failed/cancelled.

## Checkpoint semantics

V3 adds `source_checkpoints.last_committed_message_id`.

The legacy `last_seen_message_id` is retained during migration compatibility, but V3 uses the committed cursor for durable progress.

Checkpoint advancement requires:

1. at least one route execution exists for the candidate source cursor;
2. every execution for that cursor is checkpoint-safe;
3. no earlier route execution at or below the candidate cursor remains in received/processing;
4. the source belongs to the current account.

This prevents a newer event from advancing the cursor over older unfinished work.

## Operational hierarchy

A new route execution is created only when all relevant parents are active:

- SourceRoute = active
- Source = active
- Destination = active
- Project = active

Pausing the project therefore suspends new work for all destinations below it.

## Deletion safety

`route_executions` references Source, SourceRoute and Destination with `ON DELETE RESTRICT`.

Operational entities should be paused/archived instead of hard-deleted while execution evidence exists. This prevents a configuration deletion from silently erasing pending work and allowing an unsafe checkpoint jump.

Account deletion still cascades the tenant data intentionally.

## First V3 migration

Revision:

`20261007_01`

It assumes the current V2 PostgreSQL schema already exists and performs an additive migration:

- creates PostgreSQL enum `route_execution_status`;
- adds `source_checkpoints.last_committed_message_id`;
- backfills the committed cursor from the legacy seen cursor;
- creates `route_executions`;
- creates indexes and updated-at trigger.

This is a transition migration, not a fresh-database V3 bootstrap.

## Verification contract

CI uses PostgreSQL 16 and performs:

1. bootstrap current V2 reference schema;
2. `alembic upgrade head`;
3. `alembic downgrade base`;
4. `alembic upgrade head` again;
5. run route-execution PostgreSQL integration tests.

Integration coverage includes:

- one source event fan-out to multiple destinations;
- registration idempotency;
- one unfinished route blocking checkpoint movement;
- queued work being checkpoint-safe;
- older unfinished event blocking a newer cursor;
- account isolation;
- paused project suppressing new route executions;
- final execution not re-entering queue.

## Boundaries

Phase 2 does not:

- listen to Telegram in V3;
- process content;
- implement new deduplication logic;
- create publish jobs from RouteExecution;
- switch Railway production to V3.

Those remain later phase gates.
