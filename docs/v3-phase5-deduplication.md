# V3 Phase 5 — Deduplication

Date: 2026-10-08  
Branch: `rebuild/v3-foundation-20261007`  
Verified code head: `e93e1b446a1f9a88bef7cda082ffaf25c52d6e5e`  
CI run: `37699795962` — success

## Goal

Build the V3 deduplication layer between route-specific content processing and the publish queue, with typed signals, explicit scope, time windows and concurrency-safe database behavior.

Phase 5 does not enqueue or publish Telegram messages.

## Boundary

```text
RouteExecution.ready_for_dedup
        +
ProcessedContent
        |
        v
DeduplicationPolicyResolver
        |
        v
FingerprintBuilderV3
        |
        v
PostgreSQL advisory lock
        |
        v
time-window duplicate lookup
        |
        +---- duplicate ----> RouteExecution.duplicate
        |
        v
record fingerprints
        |
        v
RouteExecution.ready_for_queue
```

## V2 defect addressed

V2 generated:
- a text hash even when normalized text was empty;
- a media hash even when the media set was empty.

That made unrelated text-only posts share the same media hash and created a symmetric risk for media-only posts through the empty text hash.

V3 forbids those signals by construction.

## Typed fingerprints

Supported types:

| Type | Created when |
| --- | --- |
| `telegram_identity` | exact Telegram identity comparison is enabled |
| `text` | text comparison is enabled and normalized text is non-empty |
| `media` | media comparison is enabled and at least one media identity exists |
| `combined` | at least one meaningful content signal exists |

All stored fingerprint values are SHA-256 digests.

### Telegram identity

Canonical input:

```text
telegram:{source_id}:{event_key}
```

This keeps exact source/message or source/album identity separate from content similarity.

### Text

Text fingerprints use stripped normalized text from Phase 4.

Destination branding is not included because Phase 4 separates `normalized_text` from `rendered_text`.

### Media

Media fingerprints serialize the ordered media identity list.

Album order therefore affects the fingerprint.

### Combined

The combined signal hashes the enabled, meaningful text/media fingerprint components only.

Empty components are never added to the combined payload.

## Policy resolution

Precedence:

1. route DeduplicationProfile;
2. destination DeduplicationProfile;
3. V3 default policy.

Default policy:
- enabled;
- 24-hour window;
- compare Telegram identity;
- compare text;
- compare media;
- scope: `route`.

## Scope contract

### Route scope — default

```text
route:{route_id}
```

This isolates content comparison to one source-to-destination route.

It prevents accidental cross-source duplicates.

### Destination scope — explicit

A profile can request:

```json
{
  "scope": "destination"
}
```

The scope becomes:

```text
destination:{destination_id}
```

This deliberately enables cross-source comparison for routes publishing into the same destination.

## Time-window contract

For text/media/combined fingerprints, matching is limited to:

```text
observed_at >= now - window_seconds
```

`window_seconds` must be positive.

Telegram identity is exact identity and is not treated as an expiring content similarity signal.

## Concurrency contract

V3 uses transaction-scoped PostgreSQL advisory locks.

The lock key includes:
- account id;
- scope key;
- fingerprint type;
- fingerprint value.

All signal lock keys are acquired in sorted order.

Inside the same transaction V3:
1. locks candidate signals;
2. checks prior matching observations;
3. writes the current observations;
4. persists the RouteExecution outcome.

This prevents two matching events processed concurrently from both being accepted as unique.

## Persistence

Phase 5 adds `route_fingerprints`.

Columns include:
- account id;
- RouteExecution id;
- scope key;
- fingerprint type;
- fingerprint value;
- observation timestamp.

Constraints:
- one observation of each fingerprint type per RouteExecution;
- indexed lookup by account/scope/type/value/time.

The table is separate from V2 `content_fingerprints`.

Reasons:
- V2 fingerprints require ContentItem;
- V2 uniqueness is permanent across scope/type/value;
- permanent uniqueness conflicts with V3 time-window semantics;
- Phase 4 content is route-specific.

## RouteExecution state

Migration `20261008_03_deduplication.py` adds:

`ready_for_queue`

Relevant transitions:

```text
ready_for_dedup
    -> duplicate
    -> ready_for_queue
    -> failed

ready_for_queue
    -> queued
    -> failed
    -> cancelled
```

### Checkpoint semantics

`ready_for_queue` is neither final nor checkpoint-safe.

A unique route must wait for Phase 6 to create a durable queue representation.

`duplicate` is final and checkpoint-safe.

Therefore:
- a duplicate can safely finish that route;
- a unique route cannot advance the source committed cursor merely because dedup passed.

## Reason codes

### Accepted

- `dedup_unique`
- `dedup_disabled`
- `dedup_no_signals`

### Duplicate

- `duplicate_exact_identity`
- `duplicate_matching_text`
- `duplicate_matching_media`
- `duplicate_matching_combined`

### Failure

- `deduplication_error`

## Runtime wiring

Runtime V3 now builds:

```text
DeduplicationCoordinator
        ^
        |
ContentProcessingCoordinator
        ^
        |
TelegramIngestionComponent
```

Phase 4 calls the dedup coordinator only for `READY_FOR_DEDUP` processing results.

## Verification

GitHub Actions Run `37699795962` completed successfully at code head:

`e93e1b446a1f9a88bef7cda082ffaf25c52d6e5e`

Passed:
- 31 focused V3 contract tests;
- 109 non-integration tests, 16 deselected;
- Ruff;
- compileall;
- PostgreSQL 16 V2-reference schema bootstrap;
- Alembic upgrade through `20261008_03`;
- Alembic downgrade to base;
- Alembic re-upgrade through `20261008_03`;
- 15 PostgreSQL V3 integration tests;
- V3 CLI;
- Docker build.

## Regression and integration evidence

Verified:
- two different text-only messages do not collide through an empty media fingerprint;
- media-only messages do not emit an empty text fingerprint;
- a real repeated text is rejected;
- a real repeated media identity is rejected;
- album/media ordering participates in media fingerprints;
- content matches expire after the configured window;
- route scope is the default;
- destination scope explicitly enables cross-source deduplication;
- two concurrent matching messages produce one unique winner and one duplicate;
- duplicate outcomes remain checkpoint-safe;
- unique outcomes remain blocked at `ready_for_queue` until Phase 6.

## Explicit exclusions

Phase 5 does not implement:
- PublishJob creation;
- queue claim/leases;
- retry scheduling;
- FloodWait retry policy;
- stuck-job recovery;
- media staging;
- Bot API publishing;
- production migration;
- real Telegram V3 pilot.

These begin in Phase 6/7.

## Production safety

No Railway configuration, deployment branch, persistent volume, Telegram session or production PostgreSQL migration was changed.

Production remains `main` + Runtime V2.
