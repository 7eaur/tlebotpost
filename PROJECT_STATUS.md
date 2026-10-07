# Project Status — Telegram Relay Rebuild

Date: 2026-10-07
Rebuild branch: `rebuild/v3-foundation-20261007`
Production baseline: `main@0990fb62b97c09a3fa44e41fe3c087bb5d3fd2cf`

## Current phase

Phase 3 — Telegram Ingestion

Status: COMPLETE / VERIFIED IN CI  
External real-Telegram V3 pilot: DEFERRED UNTIL AN ISOLATED V3 SESSION IS AVAILABLE

## Production state

- Railway production remains on `main`.
- No V3 rebuild commit has changed the live service.
- Runtime V2 remains the production reference.
- The current production Telegram session/volume was deliberately not moved or shared with V3.
- The known V2 false-deduplication defect remains isolated from the rebuild path.

## Completed rebuild phases

### Phase 0 — Audit and contracts

Completed:
- current-system audit;
- product behavior reconstruction;
- target architecture;
- rebuild master plan;
- confirmed V2 duplicate-fingerprint defect.

### Phase 1 — Runtime foundation

Completed and verified:
- canonical `python -m app.v3` runtime;
- centralized V3 settings;
- lifecycle state machine;
- PostgreSQL readiness;
- healthcheck;
- Alembic;
- Docker/Compose V3 path;
- CI gates.

### Phase 2 — Core domain and route execution

Completed and verified:
- reuse of Account/Project/TelegramAccount/Source/Destination/SourceRoute;
- durable `RouteExecution`;
- route-event idempotency;
- explicit execution-state machine;
- `last_committed_message_id`;
- checkpoint anti-skip rules;
- additive migration `20261007_01`;
- PostgreSQL integration.

## Phase 3 delivered

### Telegram adapter boundary

Added:
- `TelegramUserAdapter` protocol;
- `TelethonUserAdapter` production implementation;
- explicit connect/disconnect;
- latest-message lookup;
- source subscriptions;
- disconnect monitoring.

### Typed logical source events

Added `SourceEvent` with:
- account/source identity;
- chat id;
- ordered Telegram message ids;
- grouped id;
- logical cursor;
- raw messages;
- received timestamp.

### Album handling

Added `AlbumCollectorV3`:
- one logical event per Telegram album;
- message ordering;
- duplicate album-part suppression;
- source-order preservation;
- flush on reload/shutdown;
- pending album retained until durable persistence succeeds;
- failed timer persistence keeps state and retries.

### Live-only baseline

At startup/reconnect:
- Telegram latest id becomes the live seen floor;
- `last_seen_message_id` moves monotonically;
- `last_committed_message_id` is untouched;
- history/downtime messages are not replayed;
- the effective in-memory floor never moves below the DB floor.

### Durable ingestion handoff

For a new logical event:
- V3 registers RouteExecution fan-out first;
- registration remains idempotent;
- only after successful transaction does the live seen cursor advance;
- content processing is not yet invoked.

### Active-source hierarchy

Subscriptions require active:
- Account;
- TelegramAccount;
- Project;
- Source;
- Destination;
- SourceRoute.

Reloading after a project/route pause removes obsolete subscriptions.

### Telegram account lifecycle

Connection state is synchronized to PostgreSQL:
- successful session -> active;
- disconnect -> disconnected;
- unauthorized session -> reauth_required;
- disabled Telegram account blocks startup.

Partial startup/reconnect failures clean up the Telegram connection. Failure to persist an operational status does not kill reconnect handling.

### Reconnect lifecycle

Unexpected disconnection:
1. stops callback acceptance;
2. tries to persist pending album events;
3. removes subscriptions;
4. records disconnect status when DB is available;
5. reconnects with configured backoff;
6. cleans partial attempts;
7. reloads sources;
8. applies a new live baseline;
9. resumes listening.

## Verification evidence

Verified code head:

`31522833f28fb61b2dd6f812ba797538b208d073`

GitHub Actions:

- Run ID: `37690825733`
- Conclusion: `success`

Passed:

- focused V3 foundation/domain tests: 16;
- full non-integration suite: 94 passed, 8 deselected;
- Ruff;
- compileall;
- PostgreSQL 16 schema bootstrap;
- Alembic upgrade;
- Alembic downgrade;
- Alembic re-upgrade;
- PostgreSQL V3 domain + ingestion integration: 7 passed;
- V3 CLI;
- Docker build.

Integration evidence covers:
- initial live baseline;
- old-event rejection;
- new single event registration;
- album registration;
- seen vs committed cursor separation;
- reconnect;
- downtime history skip;
- first post-reconnect event;
- Telegram account status;
- paused-project subscription removal.

## External Telegram pilot

Not executed for V3 yet.

The live authorized session is stored on the production V2 Railway volume. The available Railway operations do not provide a safe session-file clone into an isolated V3 service. Moving that volume or switching the production service would violate the production-isolation rule.

The external V3 pilot remains a controlled future validation step once an isolated V3 Telegram session exists. This is explicitly documented and is not being presented as completed.

## Source of truth

- `docs/REBUILD_MASTER_PLAN.md`
- `docs/v3-phase1-runtime-foundation.md`
- `docs/v3-phase2-core-domain.md`
- `docs/v3-phase3-telegram-ingestion.md`
- `backend/migrations/README.md`
- `PROJECT_STATUS.md`

## Next phase

Phase 4 — Content Processing

Planned work:
- route-policy resolution;
- normalized content model;
- Arabic/text-safe normalization;
- source-right/credit removal;
- Telegram/general URL handling;
- line-break/whitespace policy;
- emoji preservation policy;
- media-type classification;
- include/exclude filters;
- route/destination branding resolution;
- deterministic processing result with reason codes;
- transition RouteExecution from received -> processing -> filtered or ready-for-dedup;
- unit/property-style contract tests for text, captions, media and albums;
- PostgreSQL integration for route-profile isolation.

Phase 4 does NOT implement content deduplication or target publishing. Those remain Phase 5+.
