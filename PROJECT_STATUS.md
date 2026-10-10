# Project Status — Telegram Relay Rebuild

Date: 2026-10-08
Rebuild branch: `rebuild/v3-foundation-20261007`
Production baseline: `main@0990fb62b97c09a3fa44e41fe3c087bb5d3fd2cf`

## Current phase

Phase 9 — Observability and Operations

Status: COMPLETE / VERIFIED IN CI  
Real Telegram sandbox proof: DEFERRED UNTIL AN ISOLATED V3 SESSION IS AVAILABLE  
External real-Telegram V3 pilot: DEFERRED UNTIL AN ISOLATED V3 SESSION IS AVAILABLE

## Production state

- Railway production remains on `main`.
- No V3 rebuild commit has changed the live service.
- Runtime V2 remains the production reference.
- The current production Telegram session/volume was deliberately not moved or shared with V3.
- The known V2 false-deduplication defect remains isolated from the rebuild path.
- V3 Phases 4-9 were verified only in CI/disposable PostgreSQL/fake Telegram adapters; no production migration or V3 Telegram target publish was run.

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

### Phase 3 — Telegram ingestion

Completed and verified:
- `TelegramUserAdapter` / `TelethonUserAdapter`;
- typed `SourceEvent`;
- album aggregation;
- live-only startup/reconnect baseline;
- durable route fan-out before seen-cursor advancement;
- Telegram-account operational state;
- reconnect lifecycle;
- active source/project/destination/route hierarchy;
- PostgreSQL integration.

## Phase 4 delivered

### Route-specific processing boundary

The Phase 3 `on_registration` handoff now invokes a dedicated V3 content-processing coordinator after durable RouteExecution registration.

Each route is processed independently. Processing does not live inside the Telethon adapter and does not change Telegram ingestion semantics.

### Route policy resolution

Added account-scoped resolution for:
- `FilterProfile`;
- `TransformProfile`;
- route-level `BrandingProfile`;
- destination branding fallback.

Route branding overrides destination branding when both are configured.

### Normalized content contract

Added typed contracts for:
- normalized text;
- rendered/publish text;
- normalized media descriptors;
- content type;
- processing decision;
- deterministic reason code.

The normalized text is deliberately kept separate from branded/rendered text so future deduplication does not accidentally fingerprint destination branding.

### Arabic/text-safe normalization

Implemented deterministic handling for:
- Arabic text;
- CRLF/LF normalization;
- whitespace trimming;
- bounded blank lines;
- optional line-break flattening;
- emoji preservation by default;
- optional emoji removal without broad Arabic-codepoint stripping.

### Rights and URL handling

Implemented:
- trailing source/right/credit block removal;
- Telegram-link removal;
- general URL removal;
- independent Telegram/general URL switches through filter custom rules.

Rights removal is suffix-oriented instead of deleting arbitrary matching content in the body.

### Media and album classification

Implemented normalized media descriptors for:
- photo;
- video;
- document;
- audio;
- voice;
- unknown media.

Albums remain one logical content unit, preserve source order, and expose stable media identities without downloading/staging files in Phase 4.

### Filters

Implemented deterministic route filtering with reason codes:
- `missing_include_keyword`;
- `matched_exclude_keyword`;
- `media_type_not_allowed`;
- `empty_content`.

### Branding

Branding is rendered only after normalization/filtering.

Supported:
- footer;
- link;
- separator;
- route override;
- destination fallback.

### RouteExecution handoff state

Added `ready_for_dedup` through additive migration `20261008_02`.

Processing flow is now:

```text
received
  -> processing
      -> filtered
      -> ready_for_dedup
      -> failed
```

`ready_for_dedup` is intentionally:
- not final;
- not checkpoint-safe.

This prevents the source committed checkpoint from advancing before Phase 5+ has recorded a durable downstream outcome.

Filtered/failed outcomes continue to participate in checkpoint advancement under the existing Phase 2 coordinator.

### Runtime integration

`RuntimeV3.from_settings()` wires `ContentProcessingCoordinator.process_registration` into the existing Phase 3 registration callback.

No production/Railway command was changed.

## Phase 4 persistence boundary

Phase 4 persists processing state on `RouteExecution`, but it does not yet persist a route-specific transformed content payload or staged media.

The processed result is passed through the application boundary for the next stage. Durable dedup/queue/media handoff and crash recovery remain Phase 5+ responsibilities.

This is acceptable for the isolated rebuild phase, but V3 must not cut over to production until downstream durability is completed and exercised end-to-end.

## Verification evidence

Verified code head:

`621ba5e13f137bee0ac6c8b57674e2129753b31c`

GitHub Actions:

- Run ID: `37694872101`
- Conclusion: `success`

Passed:

- focused V3 phase-contract tests: 24 passed;
- full non-integration suite: 102 passed, 10 deselected;
- Ruff: all checks passed;
- compileall;
- PostgreSQL 16 V2-reference schema bootstrap;
- Alembic upgrade to `20261008_02`;
- Alembic downgrade to base;
- Alembic re-upgrade to `20261008_02`;
- PostgreSQL V3 domain + ingestion + content integration: 9 passed;
- V3 CLI;
- Docker build.

Phase 4 integration evidence covers:
- route-profile isolation;
- route branding over destination branding;
- destination branding fallback;
- route-specific emoji policy;
- independent normalization outcomes;
- `ready_for_dedup` persistence;
- all-filtered fan-out allowing committed checkpoint advancement.

## Phase 5 delivered

### Typed fingerprint contract

V3 now emits explicit fingerprint types:
- `telegram_identity`;
- `text`;
- `media`;
- `combined`.

The V2 empty-component defect is removed by contract:
- no text fingerprint is created when normalized text is empty;
- no media fingerprint is created when the media set is empty;
- combined fingerprints are created only from meaningful enabled content signals.

### Scoped deduplication

Default V3 content-dedup scope is `route`.

This prevents accidental cross-source comparison.

Cross-source deduplication is enabled only when a profile explicitly sets:

```json
{"scope": "destination"}
```

Route-level DeduplicationProfile overrides destination fallback.

### Time windows

Content fingerprints are matched only inside the current profile `window_seconds`.

Telegram identity remains an exact identity signal and is not reduced to a content-window comparison.

### Concurrency protection

Dedup decisions are protected with PostgreSQL transaction-scoped advisory locks derived from:
- account;
- dedup scope;
- fingerprint type;
- fingerprint value.

Matching checks and fingerprint recording occur in the same database transaction.

The PostgreSQL integration suite verifies that two matching messages processed concurrently produce exactly one unique winner and one duplicate outcome.

### Durable fingerprint observations

Added `route_fingerprints`, linked to `RouteExecution`.

It records:
- account;
- route execution;
- scope key;
- fingerprint type;
- fingerprint value;
- observation timestamp.

This table is separate from the V2 `content_fingerprints` table because V2 storage is ContentItem-bound and its permanent uniqueness constraint does not model the V3 time-window contract correctly.

### RouteExecution handoff

Migration `20261008_03` adds:

`ready_for_queue`

Phase 5 state flow:

```text
ready_for_dedup
  -> duplicate
  -> ready_for_queue
  -> failed
```

`ready_for_queue` is intentionally:
- not final;
- not checkpoint-safe.

A unique item therefore cannot advance `last_committed_message_id` until Phase 6 has persisted the publish-queue outcome.

A duplicate is final/checkpoint-safe and uses the existing checkpoint coordinator.

### Reason codes

Unique / bypass:
- `dedup_unique`;
- `dedup_disabled`;
- `dedup_no_signals`.

Duplicate:
- `duplicate_exact_identity`;
- `duplicate_matching_text`;
- `duplicate_matching_media`;
- `duplicate_matching_combined`.

Failure:
- `deduplication_error`.

### Runtime integration

`RuntimeV3.from_settings()` now wires:

```text
ContentProcessingCoordinator
        -> DeduplicationCoordinator
```

No queue/publisher behavior is introduced by Phase 5.

## Phase 5 verification evidence

Verified code head:

`e93e1b446a1f9a88bef7cda082ffaf25c52d6e5e`

GitHub Actions:

- Run ID: `37699795962`
- Conclusion: `success`

Passed:
- focused V3 phase-contract tests: 31 passed;
- full non-integration suite: 109 passed, 16 deselected;
- Ruff: all checks passed;
- compileall;
- PostgreSQL 16 V2-reference schema bootstrap;
- Alembic upgrade through `20261008_03`;
- Alembic downgrade to base;
- Alembic re-upgrade through `20261008_03`;
- PostgreSQL V3 domain + ingestion + content + dedup integration: 15 passed;
- V3 CLI;
- Docker build.

Phase 5 regression/integration evidence covers:
- distinct text-only messages are not false duplicates from empty media;
- media-only messages are not false duplicates from empty text;
- real matching text is rejected with typed reason;
- real matching media is rejected with typed reason;
- content duplicate windows expire correctly;
- destination scope enables explicit cross-source deduplication;
- concurrent matching messages produce one unique winner and one duplicate.

## Phase 5 boundary

Phase 5 does not:
- create PublishJob rows;
- claim jobs;
- retry/failover publishing;
- stage Telegram media;
- call Bot API;
- alter Railway production.

Processed publish payload durability/recovery must be completed as part of the Phase 6 queue handoff before V3 is eligible for production cutover.

## Phase 6 delivered

### Durable source-event snapshot

The ingestion registration transaction now also persists `source_event_snapshots`.

The snapshot contains only the processing inputs needed for deterministic recovery:
- source/event identity;
- ordered Telegram message ids;
- grouped id;
- chat id;
- raw text/caption;
- media type/identity metadata;
- received timestamp.

This closes the crash window where `last_seen_message_id` had advanced after durable RouteExecution registration but content processing had not completed.

Recovery does not replay Telegram history. It resumes from the durable snapshot already accepted by V3.

### Durable route publish payload

Added `route_publish_payloads`, one payload per RouteExecution.

It persists:
- normalized text;
- rendered/branding text;
- content type;
- ordered media descriptors.

The payload is written in the same database transaction that moves the execution to `ready_for_dedup`.

This makes Phase 4 -> Phase 5 -> Phase 6 recoverable after restart without keeping transformed content only in process memory.

### V3 queue handoff

The existing `publish_jobs` table is extended rather than duplicated.

V3 jobs add:
- `route_execution_id`;
- `route_payload_id`;
- `max_attempts`;
- `lease_expires_at`.

`content_item_id` is nullable so V3 route-specific payloads do not have to be forced into the V2 ContentItem contract.

One unique PublishJob is allowed per RouteExecution.

A RouteExecution moves from `ready_for_queue` to `queued` only after the durable job exists. The source checkpoint may advance only after this durable representation is present.

### Scheduling and manual mode

Publishing mode precedence:
1. route publishing mode;
2. destination publishing mode.

Supported Phase 6 queue states:
- direct -> queued immediately;
- scheduled -> queued for the resolved schedule;
- manual -> `manual_hold` until explicit release.

Manual jobs are durable and are not claimable by workers before release.

### Claim and lease

V3 workers claim only V3 jobs carrying both:
- `route_execution_id`;
- `route_payload_id`.

This prevents V3 workers from claiming legacy V2 queue rows.

Due jobs are claimed with PostgreSQL `FOR UPDATE SKIP LOCKED`.

A claim records:
- worker id;
- locked timestamp;
- lease expiry;
- incremented attempt count;
- PublicationAttempt STARTED row.

Leases can be renewed by the owning worker.

### Retry / FloodWait contract

Retries use deterministic exponential backoff with a configured cap.

A Telegram RetryAfter/FloodWait value is treated as a minimum retry delay and is never shortened by the normal backoff cap.

Jobs stop retrying when `max_attempts` is exhausted and move to failed.

### Stale-work recovery

Expired worker leases are recovered:
- to `retry_wait` when attempts remain;
- to `failed` when max attempts are exhausted.

Runtime V3 now starts a reliability recovery component before Telegram ingestion.

It periodically recovers:
1. expired queue leases;
2. `received/processing` RouteExecutions from source-event snapshots;
3. `ready_for_dedup` executions from durable route payloads;
4. `ready_for_queue` executions into idempotent PublishJobs.

### Checkpoint catch-up

Phase 6 testing exposed an ordering edge case: a later cursor could become queue-safe before an older blocker cleared.

`SourceCheckpointCoordinator` now catches up automatically to the highest fully safe cursor before the next blocking execution.

It still never jumps over unfinished work.

### Migration

Added `20261008_04_publish_queue_reliability.py`.

It adds:
- `source_event_snapshots`;
- `route_publish_payloads`;
- V3 PublishJob references/reliability fields;
- `manual_hold` job status;
- V3 due-job index.

Because `job_status` belongs to the V2 reference schema, the Phase 6 downgrade treats the added PostgreSQL enum label as monotonic: V3 rows/tables/columns are removed and any manual jobs are mapped to `queued`, while the inert enum label may remain.

This avoids unsafe reconstruction of a V2-owned PostgreSQL enum.

## Phase 6 verification evidence

Verified code head:

`d58cac213f9f87afb0a0b3f611e4dbb998ebb9d8`

GitHub Actions:

- Run ID: `37707590801`
- Conclusion: `success`

Passed:
- focused V3 contract tests: 39 passed;
- full non-integration suite: 117 passed, 21 deselected;
- Ruff: all checks passed;
- compileall;
- PostgreSQL 16 V2-reference schema bootstrap;
- Alembic upgrade through `20261008_04`;
- Alembic downgrade to base;
- Alembic re-upgrade through `20261008_04`;
- PostgreSQL V3 integration through queue reliability: 20 passed;
- V3 CLI;
- Docker build.

Phase 6 integration evidence covers:
- durable route payload + idempotent one-job handoff;
- source-event snapshot recovery;
- crash recovery from `received`;
- crash recovery from `ready_for_dedup`;
- crash recovery from `ready_for_queue`;
- checkpoint catch-up after out-of-order completion;
- RetryAfter-aware scheduling;
- max-attempt exhaustion;
- expired-lease recovery;
- manual hold/release;
- V3-only job claiming.

## Phase 6 boundary

Phase 6 does not:
- call the Telegram Bot API;
- stage/download Telegram media bytes;
- publish text/media/albums;
- map Telegram publishing errors;
- mark successful jobs published from a real target;
- alter Railway production.

Those are Phase 7 responsibilities.

## Phase 7 delivered

### V3 publisher worker

Added a dedicated V3 publisher component that:
- claims only V3 PublishJobs;
- loads the durable RoutePublishPayload;
- publishes through the Telegram Bot API;
- records successful publication state;
- integrates with the existing lease/retry contract.

The publisher remains opt-in through `V3_PUBLISHER_ENABLED`.

### Shared authorized user session

Media acquisition reuses the same connected `TelethonUserAdapter` instance used by V3 ingestion.

No second Telegram user session is opened.

Media is fetched by exact source `chat_id + message_id`; this is targeted acquisition for already-accepted events and is not history replay.

### Supported publishing shapes

Implemented:
- text;
- photo;
- video;
- document;
- audio;
- voice;
- photo/video albums;
- document-only albums;
- audio-only albums.

Albums remain one PublishJob and one logical RouteExecution.

### Telegram text and caption limits

Plain text is split deterministically at Telegram's message limit without truncating content.

For media:
- rendered text within the caption limit is sent as the media caption;
- longer rendered text publishes the media without caption and sends the complete text afterward in safe chunks.

### Transient media lifecycle

Media bytes are downloaded into attempt-scoped temporary staging directories.

On completion or failure:
- attempt staging is removed;
- stale managed staging directories are cleaned at publisher startup.

No media bytes are committed to Git or PostgreSQL.

### Published-message durability

Successful jobs persist a `PublishedMessage`.

Metadata records:
- every Telegram target message id;
- message count;
- partial/success state;
- V3 marker.

`PublicationAttempt` is completed as succeeded and `RouteExecution` moves to `published`.

### Error and retry behavior

Implemented explicit classification for:
- RetryAfter;
- timeout/network failures;
- Forbidden;
- BadRequest;
- generic Telegram errors;
- media acquisition failures.

RetryAfter can safely schedule a retry when no target message was confirmed.

### Partial/unknown publish safety

Telegram Bot API has no idempotency key.

V3 therefore does not claim false exactly-once semantics.

If at least one target message was confirmed before a later send failed:
- the confirmed ids are persisted;
- the job fails closed;
- V3 does not automatically replay the entire post.

If a worker lease expires after the job entered `publishing` and the external result is unknown:
- the job fails with `publish_outcome_unknown`;
- automatic retry is disabled to avoid duplicate target posts.

A lease expiry while still in pre-send `processing` remains safely retryable under Phase 6.

### Runtime integration

When `V3_PUBLISHER_ENABLED=true`, Runtime V3 wires:
1. queue recovery;
2. Telegram ingestion/user-session connection;
3. publisher worker using that same user adapter for media acquisition.

Publisher configuration is separate from ingestion configuration and requires `BOT_TOKEN`.

## Phase 7 verification evidence

Verified code head:

`1c2c7f7c63e8461bc65dc3a8113122e5075cb552`

GitHub Actions:

- Run ID: `37848919833`
- Conclusion: `success`

Passed:
- focused V3 phase-contract tests: 51 passed;
- full non-integration suite: 129 passed, 27 deselected;
- Ruff: all checks passed;
- compileall;
- PostgreSQL 16 V2-reference schema bootstrap;
- Alembic upgrade/downgrade/re-upgrade through `20261008_04`;
- PostgreSQL V3 integration through publisher: 26 passed;
- V3 CLI;
- Docker build.

Phase 7 integration evidence covers:
- text publication and durable PublishedMessage state;
- long media-caption fallback without content truncation;
- media staging and cleanup;
- album as one logical queue job;
- RetryAfter retry state;
- partial-publish fail-closed behavior;
- expired `publishing` lease -> `publish_outcome_unknown` instead of replay.

## Phase 7 boundary

Phase 7 does not claim real Telegram E2E proof.

The Bot API/user-session behavior is verified with fake adapters plus PostgreSQL integration, while the real authorized V3 sandbox session remains unavailable.

No Railway deployment, production session, production PostgreSQL migration or target-channel publishing was performed.

## Phase 8 delivered

### PostgreSQL-backed control plane

Added `ControlServiceV3` as the application-layer source of truth for operator actions.

The Telegram control bot is intentionally thin and does not own business rules.

Supported control operations:
- runtime/system status;
- project listing and pause/resume;
- source listing/add/enable/disable;
- destination listing/add/enable/disable;
- route listing/add/enable/disable;
- manual PublishJob release;
- explicit safe ingestion reload.

### Owner-only Telegram surface

`ControlBotV3` accepts commands only when:
- Telegram user id matches the configured owner;
- the command arrives in a private chat.

Unauthorized users do not reach control services.

### Live-only source add contract

Adding a source resolves the Telegram chat through the authorized user session and captures the current newest Telegram message id.

That id is persisted as the SourceCheckpoint `last_seen_message_id` before runtime reload.

Therefore adding a source through the control bot does not replay historical messages.

### Shared chat resolver

Telegram reference parsing/resolution was moved into a shared `app.telegram.chat_resolver` helper.

Both legacy control code and V3 use the same resolution behavior for:
- @username;
- t.me links;
- invite links;
- numeric/internal chat references.

V3 resolves chats through the already-connected Telethon user adapter.

### Safe reload

Configuration mutations commit to PostgreSQL first, then call `TelegramIngestionComponent.reload(rebaseline=False)`.

Reload:
- temporarily stops acceptance;
- flushes pending albums;
- replaces subscriptions;
- reloads SourceCheckpoint seen floors;
- does not replay Telegram history.

If persistence succeeds but reload fails, the owner receives a distinct `configuration_saved_runtime_reload_failed` result and can explicitly run `/reload`.

### Project-level pause/run

Global operator pause/run is represented by Project status instead of mass-changing every route.

This avoids accidentally reactivating a SourceRoute that was intentionally paused on its own.

### Control commands

Implemented:
- `/status`, `/health`;
- `/projects`, `/sources`, `/destinations`, `/routes`;
- `/addsource`;
- `/source_on`, `/source_off`;
- `/adddestination`;
- `/destination_on`, `/destination_off`;
- `/addroute`;
- `/route_on`, `/route_off`;
- `/run`, `/pause`;
- `/release`;
- `/reload`.

### Runtime enablement

Control is explicitly opt-in:

```text
V3_CONTROL_ENABLED=true
```

Owner identity comes from:
- `V3_CONTROL_OWNER_ID`, or existing `OWNER_ID` fallback.

Bot token comes from:
- `V3_CONTROL_BOT_TOKEN`, or existing `BOT_TOKEN` fallback.

When V3 control is enabled, the Telegram user-session/ingestion group is also enabled because source resolution and safe runtime reload depend on it.

## Phase 8 verification evidence

Verified code head:

`4c8229ad5f9372ec277841f0c295b5922ebd000a`

GitHub Actions:

- Run ID: `37860895897`
- Conclusion: `success`

Passed:
- focused V3 phase-contract tests: 58 passed;
- full non-integration suite: 136 passed, 29 deselected;
- Ruff: all checks passed;
- compileall;
- PostgreSQL 16 V2-reference schema bootstrap;
- Alembic upgrade/downgrade/re-upgrade through `20261008_04`;
- PostgreSQL V3 integration through control plane: 28 passed;
- V3 CLI;
- Docker build.

Phase 8 integration evidence covers:
- current-baseline source creation;
- destination creation in the active project;
- source-to-destination route creation;
- project pause/resume;
- source/destination/route pause/resume;
- safe reload callback after every runtime-affecting mutation;
- manual PublishJob release;
- queue/system status;
- owner-only/private-chat authorization.

## Phase 8 boundary

Phase 8 does not:
- perform Telegram account login/OTP flows through the control bot;
- expose secrets/session material;
- deploy V3 to production;
- replace the real V2 control bot yet.

V3 account authorization remains an operator/bootstrap concern until migration/cutover. Normal post-bootstrap operation no longer requires manual database edits.

## Phase 9 delivered

### Secret-safe structured logging

V3 runtime logging now uses a dedicated safe formatter.

Default:

`V3_LOG_FORMAT=json`

Optional:

`V3_LOG_FORMAT=text`

The JSON formatter emits only:
- UTC timestamp;
- level;
- logger name;
- rendered log message;
- exception type/message when present.

It does not serialize arbitrary LogRecord extras or full traceback objects.

Configured sensitive values are redacted before output, including:
- database connection URL/password;
- Telegram API hash;
- publisher bot token;
- control bot token.

### Content-free diagnostics

Added `ObservabilityServiceV3`.

`diagnose_job()` reads only:
- PublishJob ids/status/times/reason code;
- RouteExecution id/status/reason code;
- destination/source-route ids;
- attempt status/error codes/message ids/latency;
- PublishedMessage Telegram ids.

It deliberately does not load or return RoutePublishPayload text/captions or stored error messages.

### Metrics

Added account-scoped counters for:
- V3 PublishJobs grouped by status;
- RouteExecutions grouped by status;
- publication-attempt total;
- SystemEvent total.

Control Bot commands:
- `/metrics`;
- `/job JOB_UUID`.

### Safe SystemEvent details

Operational events may contain primitive IDs/counts/status values only.

Detail keys suggesting credentials, sessions or content are rejected, including token/secret/password/api_hash/session/content/caption/message text fields.

Nested arbitrary payloads are also rejected.

### Runtime lifecycle events

An observability component records:
- `runtime_ready`;
- `runtime_stopping`.

Runtime-ready hooks run only after all components have started.

Hook failure is best-effort and does not turn a healthy runtime into a failed runtime.

### Owner readiness notification

When V3 Control Bot is enabled, the owner receives a content-free readiness summary after runtime startup with:
- state;
- database readiness;
- component count;
- source/route counts;
- pending/failed queue counts.

### Operations runbook

Added `docs/v3-operations-runbook.md` covering:
- status/metrics/job diagnosis;
- reason-code-first troubleshooting;
- PostgreSQL logical backup/restore;
- Telegram session backup safety;
- restore validation in an isolated environment;
- production/cutover constraints.

## Phase 9 verification evidence

Verified code head:

`f3f11a1986b1d6d9713f9705a87db7c7e5673a89`

GitHub Actions:

- Run ID: `37861627737`
- Conclusion: `success`

Passed:
- focused V3 phase-contract tests: 63 passed;
- full non-integration suite: 141 passed, 30 deselected;
- Ruff: all checks passed;
- compileall;
- PostgreSQL V2-reference schema bootstrap;
- Alembic upgrade/downgrade/re-upgrade through `20261008_04`;
- PostgreSQL V3 integration through observability: 29 passed;
- V3 CLI;
- Docker build.

Security regression evidence includes:
- configured secrets redacted from structured log message/exception;
- arbitrary LogRecord extras are not serialized;
- diagnostic output excludes seeded sensitive message/error bodies;
- unsafe SystemEvent detail keys/values are rejected.

## Phase 9 boundary

Phase 9 does not ship external log aggregation or telemetry SaaS.

PostgreSQL SystemEvent + structured stdout/stderr + owner diagnostics are the current operational foundation.

No production backup, restore, Railway deployment or session copy was performed during this phase.

## External Telegram pilot

Not executed for V3 yet.

The live authorized session is stored on the production V2 Railway volume. The production session was not copied, moved, mounted into V3, or run concurrently.

The external V3 pilot remains a controlled future validation step once an isolated V3 Telegram session exists. This is explicitly documented and is not being presented as completed.

## Source of truth

- `docs/REBUILD_MASTER_PLAN.md`
- `docs/v3-phase1-runtime-foundation.md`
- `docs/v3-phase2-core-domain.md`
- `docs/v3-phase3-telegram-ingestion.md`
- `docs/v3-phase4-content-processing.md`
- `docs/v3-phase5-deduplication.md`
- `docs/v3-phase6-publish-queue-reliability.md`
- `docs/v3-phase7-publisher-media-lifecycle.md`
- `docs/v3-phase8-control-bot.md`
- `docs/v3-phase9-observability-operations.md`
- `docs/v3-operations-runbook.md`
- `backend/migrations/README.md`
- `PROJECT_STATUS.md`

## Next phase

Phase 10 — Fresh V3 Bootstrap + Secret/Key Carryover

Decision:
- V1/V2 application data will NOT be migrated.
- old sources, destinations, routes, profiles, queue history, content, attempts and operational records are disposable.
- V3 will start with a clean application database and fresh topology.
- only the existing runtime credentials/keys required by Telegram/control/publishing are carried forward.
- the authorized Telegram user session is treated as sensitive authentication state and may be preserved for final cutover, but it must never be used concurrently by V2 and V3.

Carryover scope:
- Telegram API application credentials;
- Bot token;
- owner Telegram id;
- fresh database connection for V3;
- fresh V3 account and Telegram-account identifiers created during bootstrap;
- V3 session path when the approved session is attached at cutover.

Explicitly excluded:
- all V2_* runtime identifiers/settings;
- V1/V2 business/application rows;
- historical content/queue/publication data;
- old brand/content settings as migration inputs.

Required work:
- build a clean V3 bootstrap/cutover checklist;
- verify required credential names are present without exposing values;
- provision a clean V3 database/schema;
- bootstrap fresh Account/Project/TelegramAccount rows and generate V3 identifiers;
- prepare session handoff with no concurrent V2/V3 ownership;
- verify V3 starts without any V2 data dependency;
- keep rollback by leaving the current V2 production deployment unchanged until the pilot/cutover gate.

Production remains untouched until the clean V3 bootstrap and pilot are verified.
