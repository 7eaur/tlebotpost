# Telegram Relay — Comprehensive Rebuild Master Plan

Status: Phases 0-9 complete in CI; Phase 10 next (real Telegram V3 pilot deferred until isolated session)
Branch: rebuild/v3-foundation-20261007
Baseline: main@0990fb62b97c09a3fa44e41fe3c087bb5d3fd2cf
Date: 2026-10-08

## 1. Product intent

The system is a Telegram content distribution/relay platform:

Telegram sources -> Telethon user-session ingestion -> live-only acceptance -> route-specific processing -> deduplication -> queue/scheduling -> Bot API publishing -> target channels.

Core guarantees:
- only new messages after the live baseline;
- no historical replay after restart unless explicitly added later;
- publish as a new message, not Forward;
- support text and common Telegram media types;
- albums stay one logical post;
- filters/branding are route-aware;
- failures are visible and recoverable;
- secrets and Telegram sessions never enter Git;
- production state survives restart.

## 2. Current-state audit

### Healthy foundations
- Railway worker deployment is stable.
- PostgreSQL is connected and Runtime V2 is live.
- persistent Telegram user session works.
- active sources/routes load and new Telegram events reach ingestion.
- queue/publisher infrastructure exists.
- account-scoped schema and repositories are useful foundations.

### Confirmed functional defect
The current deduplication emits a media fingerprint even when a message has no media. For text-only messages this hashes the empty string, producing the same fingerprint for every text-only post. The duplicate check treats any matching fingerprint as sufficient, so unrelated later text messages can be rejected as matching_fingerprint.

The symmetric risk exists for media-only messages because an empty normalized text hash is also constant.

### Architectural inconsistencies
1. V1 and V2 coexist in the same application package.
2. backend/app/main.py is still the V1 entrypoint.
3. Railway starts V2 through a custom start command rather than the repository default runtime.
4. Dockerfile defaults to V1 while docker-compose defaults to V2.
5. production performs a legacy SQLite import at every runtime start.
6. documentation contains old and new assumptions at the same time.
7. V2 persists accepted content broadly although earlier product rules preferred minimal retention.
8. V2 media publishing needs storage_key, while live ingestion currently stores media metadata only; the media path is incomplete.
9. album semantics are not yet a complete first-class V2 end-to-end path.
10. checkpoint semantics are not explicit enough when one source fans out to multiple routes.
11. the control bot is V1-oriented while production execution is V2.
12. no real Telegram end-to-end test currently proves source -> processing -> queue -> publisher -> target.

## 3. Rebuild principles

- One production runtime.
- One source of truth for configuration.
- No Railway-only business logic.
- No normal production path that mixes V1 and V2.
- Modular monolith first.
- PostgreSQL for durable configuration and operational state.
- Telethon user session for reading; Bot API for publishing.
- SourceRoute is the policy boundary between source and destination.
- Live-only behavior is explicit and testable.
- Deduplication is scoped, typed, and time-aware.
- Never create duplicate signals from empty text or an empty media set.
- A source checkpoint advances only after all intended route outcomes are durable.
- Albums are one logical content unit.
- Direct publishing may still use a durable queue for reliability.
- Media has an explicit temporary/durable staging lifecycle.
- Observability uses IDs/status/reasons, never message bodies or secrets.
- Every phase has a verification gate.

## 4. Target architecture

Telegram User Session
  -> Ingestion Adapter
  -> Source Event
  -> Route Resolver
  -> one RouteExecution per destination
  -> Content Processor
  -> Deduplication Service
  -> Publish Queue
  -> Publisher
  -> Telegram targets

Supporting modules: configuration/domain services, Telegram session manager, media staging, scheduler, control surface, audit/metrics, migrations.

## 5. Domain model

Keep and simplify these concepts:
- Account
- Project
- TelegramAccount
- Source
- Destination
- SourceRoute
- FilterProfile
- TransformProfile
- BrandingProfile
- DeduplicationProfile
- ScheduleProfile
- SourceCheckpoint
- RouteExecution / IngestionEvent
- ContentItem only when persistence is required
- PublishJob
- PublicationAttempt
- MediaAsset only when staging is required

## 6. Deduplication contract

Identity duplicate:
- exact source/message identity: telegram:{source_id}:{message_id or grouped_id}.

Content duplicate:
- optional, scoped to destination or route and a configured time window;
- hash normalized text only when non-empty;
- hash media identities only when media exists;
- combined hash only when at least one meaningful component exists;
- cross-source duplicate detection is explicit, never accidental;
- database constraints protect concurrency, while one service owns the semantics.

## 7. Checkpoint contract

For each source message:
1. identify active routes;
2. create route executions;
3. process each route independently;
4. each route ends in a durable skipped/queued/published/failed state;
5. advance source checkpoint only when every intended route is safely represented.

A failed destination must not silently lose the message for other routes.

## 8. Media and albums

Direct media: obtain through the authorized user session, stage temporarily, publish with Bot API, then clean up.
Scheduled/retry media: retain until job completion/expiry.
Albums: aggregate by grouped_id before route processing, preserve order, use one logical dedup identity and one publish job per destination.

## 9. Control model

Domain/application services are the source of truth. The Telegram bot is a thin control client over those services. A web UI or Mini App can be added later without owning business logic.

## 10. Deployment model

- one production command;
- Dockerfile, docker-compose and Railway use the same command;
- no automatic legacy import shell wrapper at every boot;
- migrations are explicit;
- legacy import is one-time and operator-triggered;
- health checks cover process/DB/runtime readiness;
- graceful shutdown stops ingestion before workers.

## 11. Rebuild phases

### Phase 0 — Freeze, audit, contracts
Document current production, product behavior, risks and target architecture. No behavior change.

### Phase 1 — Runtime foundation
Single V3 entrypoint, centralized settings, lifecycle wiring, PostgreSQL migration framework, no deployment-only bootstrap logic.
Gate: V3 boots in test/CI with fake Telegram adapters and PostgreSQL integration.

### Phase 2 — Core domain and route execution
Normalize Source/Destination/Route services, statuses, validation, route-execution and checkpoint semantics.
Gate: one source event deterministically produces the expected route executions.

### Phase 3 — Telegram ingestion
Session lifecycle, live baseline, reconnect, source subscriptions, album collector, typed source events.
Gate: simulated and real pilot ingestion receives exactly the expected new events.

### Phase 4 — Content processing — COMPLETE / VERIFIED
Normalization, source-rights/URL cleaning, branding, include/exclude/media filters, content type handling and route policy inheritance.

Delivered:
- route-scoped filter/transform/branding resolution;
- normalized text separate from rendered branding;
- Arabic/text-safe normalization;
- independent Telegram/general URL handling;
- trailing source-right/credit cleaning;
- emoji and line-break policies;
- media/album classification;
- deterministic filter reason codes;
- `ready_for_dedup` RouteExecution handoff state;
- runtime handoff from durable Phase 3 registration;
- PostgreSQL route-isolation/checkpoint tests.

Gate: PASSED in GitHub Actions Run `37694872101` at code head `621ba5e13f137bee0ac6c8b57674e2129753b31c`.

Boundary:
- no fingerprint/dedup semantics;
- no queue/publisher;
- no media download/staging;
- no production migration/cutover.

### Phase 5 — Deduplication — COMPLETE / VERIFIED
Implemented:
- typed Telegram/text/media/combined fingerprints;
- no empty text/media fingerprints;
- route scope by default;
- explicit destination scope for cross-source comparison;
- profile time windows;
- PostgreSQL advisory-lock concurrency protection;
- durable RouteExecution-linked fingerprint observations;
- `ready_for_queue` handoff state;
- typed duplicate/unique reason codes.

Gate: PASSED in GitHub Actions Run `37699795962` at code head `e93e1b446a1f9a88bef7cda082ffaf25c52d6e5e`.

Verified regressions include the V2 empty-media/text fingerprint defect, real duplicate matching, time-window expiry, explicit cross-source scope and concurrent duplicate races.

### Phase 6 — Publish queue and reliability — COMPLETE / VERIFIED
Implemented:
- durable source-event snapshots after Telegram acceptance;
- durable route-specific processed payloads;
- idempotent one-job-per-RouteExecution queue handoff;
- direct/scheduled/manual-hold queue semantics;
- PostgreSQL SKIP LOCKED claims;
- worker leases and renewal;
- exponential retry/backoff with RetryAfter/FloodWait minimum;
- max-attempt enforcement;
- expired-lease recovery;
- periodic recovery from received/processing, ready-for-dedup and ready-for-queue;
- checkpoint catch-up without jumping over blockers;
- V3-only job claiming while legacy V2 jobs remain isolated.

Gate: PASSED in GitHub Actions Run `37707590801` at code head `d58cac213f9f87afb0a0b3f611e4dbb998ebb9d8`.

Boundary:
- no Bot API publishing;
- no media byte staging/download;
- no real Telegram target proof;
- no production migration/cutover.

### Phase 7 — Publisher and media lifecycle — COMPLETE / VERIFIED IN CI
Implemented:
- V3 publisher worker over leased PublishJobs;
- durable RoutePublishPayload loading;
- Bot API text publishing;
- targeted source-media acquisition through the same Telethon user adapter as ingestion;
- transient attempt-scoped media staging and cleanup;
- photo/video/document/audio/voice publishing;
- supported Telegram album families as one logical job;
- deterministic text splitting and caption fallback without truncation;
- Telegram RetryAfter/network/permanent-error classification;
- durable PublishedMessage + PublicationAttempt completion;
- partial-publish fail-closed behavior;
- unknown external publish outcome protection against automatic duplicate replay;
- opt-in V3 publisher runtime settings.

CI gate: PASSED in GitHub Actions Run `37848919833` at code head `1c2c7f7c63e8461bc65dc3a8113122e5075cb552`.

Verified in CI/PostgreSQL with fake Telegram adapters. Real Telegram sandbox E2E is intentionally deferred until an isolated authorized V3 session is available, so Phase 11 remains the formal real-world E2E gate.

### Phase 8 — Control Bot V3 — COMPLETE / VERIFIED IN CI
Implemented:
- PostgreSQL-backed ControlServiceV3;
- owner-only private Telegram command surface;
- project/source/destination/route listing and status controls;
- source add with current Telegram baseline;
- destination and SourceRoute creation;
- Project-level pause/run;
- manual PublishJob release;
- safe ingestion reload without historical replay;
- shared Telegram reference resolver;
- account/queue/last-error status output;
- explicit opt-in control runtime configuration.

Gate: PASSED in GitHub Actions Run `37860895897` at code head `4c8229ad5f9372ec277841f0c295b5922ebd000a`.

Normal post-bootstrap source/destination/route operation is now possible without manual PostgreSQL edits.

### Phase 9 — Observability and operations — COMPLETE / VERIFIED IN CI
Implemented:
- secret-safe JSON/text V3 logging;
- configured credential redaction;
- fixed allow-listed log output shape;
- account-scoped queue/execution/attempt/system-event metrics;
- content-free PublishJob diagnostics;
- /metrics and /job owner commands;
- safe SystemEvent detail validation;
- runtime_ready/runtime_stopping lifecycle events;
- best-effort owner startup readiness notification;
- PostgreSQL/session backup and restore runbook.

Gate: PASSED in GitHub Actions Run `37861627737` at code head `f3f11a1986b1d6d9713f9705a87db7c7e5673a89`.

The regression suite seeds sensitive message/error text in PostgreSQL and proves it is absent from operator job diagnostics. Structured-log tests prove configured tokens/passwords are redacted and arbitrary LogRecord extras are not serialized.

### Phase 10 — Migration
One-time V1/V2 migration with dry-run, data mapping, count verification and Telegram session preservation.
Gate: repeatable on a copy before production.

### Phase 11 — End-to-end pilot
Test text, image, video, document, album, multi-source, multi-target, exact duplicate, similar content, restart, reconnect, permission failure and retry/FloodWait.
Gate: recorded source-to-target evidence for all critical paths.

### Phase 12 — Production cutover and cleanup
Deploy V3, observe, switch production, keep rollback point, archive obsolete V1/V2 runtime code, update all docs.
Gate: one runtime, one DB model, one deployment command and current documentation.

## 12. Test strategy

- unit tests for normalization/filter/dedup/scheduler;
- PostgreSQL integration tests;
- runtime integration with fake Telegram adapters;
- real Telegram sandbox E2E;
- restart/recovery tests;
- migration tests;
- Docker build/health tests.

Unit-test count alone is never production readiness.

## 13. Immediate execution order

Completed:
1. freeze current production baseline;
2. create the V3 branch;
3. encode runtime/route/live-only behavior contracts;
4. build the single V3 runtime foundation;
5. build durable route execution and Telegram ingestion;
6. build route-specific content processing;
7. build typed/concurrent-safe deduplication;
8. build durable queue handoff, leases, retries and crash recovery.

Next:
1. implement Phase 10 one-time migration tooling with dry-run/count verification;
2. validate migration against an isolated production-like copy;
3. run the isolated real-Telegram pilot only with a separate authorized V3 session;
4. leave current production untouched until V3 proves the critical path and reaches the formal cutover gate.

## 14. Definition of done

The rebuild is complete only when:
- real source -> destination Telegram publishing is proven;
- text/media/albums work;
- unique messages are not falsely deduplicated;
- retries/restarts do not lose acknowledged work;
- live-only behavior is preserved;
- route changes are safe;
- no secret appears in logs or Git;
- Docker/Railway use repository-defined runtime;
- migrations are explicit;
- old runtime is archived, not accidentally executable;
- documentation matches the running system.