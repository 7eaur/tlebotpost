# Telegram Relay — Comprehensive Rebuild Master Plan

Status: Phases 0-3 complete; Phase 4 next (external V3 Telegram pilot deferred until isolated session)
Branch: rebuild/v3-foundation-20261007
Baseline: main@0990fb62b97c09a3fa44e41fe3c087bb5d3fd2cf
Date: 2026-10-07

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

### Phase 4 — Content processing
Normalization, source-rights/URL cleaning, branding, include/exclude/media filters, content type handling and route policy inheritance.
Gate: contract tests for Arabic text, links, emoji, empty captions, media and albums.

### Phase 5 — Deduplication
Implement typed fingerprints, time windows, concurrency protection and complete edge-case tests.
Gate: unique live messages are never false duplicates; deliberate duplicates are reliably rejected.

### Phase 6 — Publish queue and reliability
Durable jobs, claim/lease, retries/backoff/FloodWait, max attempts, stuck-job recovery and idempotent enqueue.
Gate: restart/crash tests prove durable processing without silent loss.

### Phase 7 — Publisher and media lifecycle
Text, photo, video, document, audio, voice, albums, staging/cleanup, caption limits and Telegram error mapping.
Gate: real Telegram pilot publishes every supported type.

### Phase 8 — Control Bot V3
Owner authorization, status, source/destination/route management, pause/resume, health/last-error and safe runtime reload.
Gate: normal operation needs no manual DB edits.

### Phase 9 — Observability and operations
Structured logs, reason codes, counters, startup readiness notification, secret-safe logging, backup/restore docs.
Gate: a failed post can be diagnosed from IDs and reason codes without logging content.

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

1. freeze current production baseline;
2. create the V3 branch;
3. encode behavior contracts in tests;
4. add the single V3 runtime skeleton;
5. rebuild deduplication against contracts;
6. continue phase-by-phase through media/publishing and real Telegram pilot;
7. leave current production untouched until V3 proves the critical path.

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