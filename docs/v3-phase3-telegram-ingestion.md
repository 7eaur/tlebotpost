# V3 Phase 3 — Telegram Ingestion

Branch: `rebuild/v3-foundation-20261007`

## Goal

Build the V3 Telegram user-session ingestion layer without introducing content transformation, deduplication, or publishing.

The Phase 3 boundary is:

```text
Telegram User Session
  -> TelegramUserAdapter
  -> live source subscription
  -> SourceEvent / album aggregation
  -> durable RouteExecution registration
  -> last_seen cursor update
```

Target publishing is intentionally absent.

## Components

### `TelegramUserAdapter`

V3 defines a small adapter contract for:

- connect/disconnect;
- latest message id lookup;
- source subscription/unsubscription;
- disconnection monitoring.

`TelethonUserAdapter` is the production implementation and uses the existing authorized Telegram user-session storage.

### `SourceEvent`

Incoming raw Telegram messages are converted into a typed logical source event.

A SourceEvent records:

- account;
- source;
- Telegram chat;
- ordered message ids;
- optional grouped/album id;
- logical cursor message id;
- raw Telegram messages;
- received timestamp.

For an album, the logical cursor is the highest message id in the group.

### `AlbumCollectorV3`

Grouped Telegram media is assembled into one logical event.

Guarantees:

- parts are ordered by Telegram message id;
- duplicate album parts are ignored;
- a different group or a normal message flushes the previous album first;
- pending albums are flushed during reload/shutdown;
- an album is removed from pending memory only after durable persistence succeeds;
- timer persistence failure keeps the album pending and schedules another attempt.

### `TelegramIngestionComponent`

The component is part of the V3 Runtime lifecycle.

It:

1. validates the configured account and Telegram account;
2. connects the authorized user session;
3. marks the Telegram account active after successful connection;
4. loads only active sources beneath active Project/Destination/Route configuration;
5. applies a live-only baseline;
6. registers one subscription per active source;
7. converts raw events into logical source events;
8. persists RouteExecution fan-out;
9. advances only the observed `last_seen_message_id`;
10. monitors disconnection and reconnects with configured delays;
11. rebaselines after reconnect so downtime history is not replayed;
12. cleans subscriptions and session state on shutdown.

## Live-only baseline contract

On initial start and reconnect:

- V3 asks Telegram for the latest message id in each active source.
- `last_seen_message_id` is advanced monotonically to that baseline.
- `last_committed_message_id` is NOT modified.
- events at or below the effective seen floor are ignored.
- messages that arrived while the process was offline are intentionally not replayed.

The in-memory baseline never moves below the durable DB baseline.

This separates:

- **seen/live floor** — where live listening begins;
- **committed cursor** — how far durable route outcomes are safe.

## RouteExecution handoff

After a complete logical source event is assembled:

- `RouteExecutionService.register_event()` creates one durable execution per active route;
- registration is idempotent by `(route_id, event_key)`;
- only after that transaction succeeds is `last_seen_message_id` updated for the live event;
- `last_committed_message_id` remains unchanged until later processing makes route outcomes checkpoint-safe.

No content decision is made in Phase 3.

## Source selection

A source is subscribed only when all relevant configuration is active:

- Account;
- TelegramAccount;
- Project;
- Source;
- Destination;
- SourceRoute.

Pausing a project or route and reloading ingestion removes that source subscription when no active route remains.

## Telegram account connection state

V3 updates the configured `TelegramAccount` operational state:

- successful user-session connection -> `active`;
- normal/lost connection -> `disconnected`;
- unauthorized stored session -> `reauth_required`;
- explicitly disabled Telegram account blocks startup.

A failure while persisting the status does not leave a partially connected Telegram client or kill the reconnect watchdog.

## Reconnect lifecycle

Unexpected disconnect performs:

1. stop accepting new callbacks;
2. try to persist pending albums;
3. remove subscriptions;
4. mark the account disconnected when DB is available;
5. retry connection using `V3_RECONNECT_DELAYS`;
6. clean any partial reconnect attempt;
7. reconnect;
8. mark Telegram account active;
9. reload active sources;
10. apply a fresh live baseline;
11. resume event acceptance.

Pending album state is retained if persistence temporarily fails.

## Configuration

Phase 3 uses:

```env
V3_TELEGRAM_ENABLED=true
V3_ACCOUNT_ID=<uuid>
V3_TELEGRAM_ACCOUNT_ID=<uuid>
V3_SESSION_PATH=data/v3.session
V3_ALBUM_WINDOW_SECONDS=0.8
V3_RECONNECT_DELAYS=5,15,30,60
API_ID=...
API_HASH=...
```

Bot credentials are not part of the ingestion dependency. Bot API concerns enter later publishing/control phases.

## Verification

Verified GitHub Actions run:

- Run ID: `37690825733`
- Code head: `31522833f28fb61b2dd6f812ba797538b208d073`
- Conclusion: `success`

Results:

- focused V3 foundation/domain tests: 16 passed;
- full non-integration suite: 94 passed, 8 integration tests deselected;
- Ruff: passed;
- compileall: passed;
- PostgreSQL 16 bootstrap: passed;
- Alembic upgrade/downgrade/re-upgrade: passed;
- PostgreSQL V3 domain + ingestion integration: 7 passed;
- V3 CLI: passed;
- Docker build: passed.

The ingestion integration suite proves with a fake Telegram transport over real PostgreSQL:

- startup live baseline;
- old messages ignored;
- new single message -> RouteExecution;
- album -> one logical RouteExecution;
- `last_seen` advances while `last_committed` does not;
- unexpected disconnect/reconnect;
- reconnect baseline skips downtime history;
- first new message after reconnect is accepted;
- TelegramAccount state becomes active after connection;
- paused project removes source subscription.

Unit coverage also proves an album is retained after a simulated persistence failure and succeeds on a later flush.

## External Telegram pilot status

A V3 real-Telegram pilot was deliberately NOT run on Railway in Phase 3.

Reason:

- production V2 owns the currently authorized Telegram session on its persistent volume;
- the available Railway operations do not provide a safe clone of that session file into an isolated V3 service;
- moving the production volume or switching the production service to the rebuild branch would violate the no-production-impact rule.

Therefore the V3 ingestion code is verified with the real Telethon adapter boundary plus deterministic fake-transport integration on PostgreSQL, while the external-session pilot is deferred until an isolated V3 session is provisioned. Production V2 remains untouched.

## Boundaries

Phase 3 does not implement:

- text normalization;
- source-right removal;
- filters;
- branding;
- deduplication;
- publish queue creation;
- target publishing.

Those are subsequent phase gates.
