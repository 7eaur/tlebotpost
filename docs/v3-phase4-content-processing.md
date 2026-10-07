# V3 Phase 4 — Content Processing

Date: 2026-10-08  
Branch: `rebuild/v3-foundation-20261007`  
Verified code head: `621ba5e13f137bee0ac6c8b57674e2129753b31c`  
CI run: `37694872101` — success

## Goal

Build the route-specific content-processing layer between durable Telegram ingestion and deduplication, without implementing deduplication, queueing, publishing, or production cutover.

The Phase 4 boundary is:

```text
SourceEvent
  + durable EventRegistration / RouteExecution
        |
        v
RoutePolicyResolver
        |
        v
ContentNormalizerV3
        |
        v
ContentFilterV3
        |
        +---- filtered ----> RouteExecution.filtered
        |
        v
BrandingRendererV3
        |
        v
ProcessingResult
        |
        v
RouteExecution.ready_for_dedup
```

## Runtime handoff

Phase 3 already exposed an `on_registration(SourceEvent, EventRegistration)` callback after RouteExecution fan-out is durably committed.

Phase 4 uses that boundary directly.

`RuntimeV3.from_settings()` creates a `ContentProcessingCoordinator` and passes `processor.process_registration` as the ingestion callback.

This preserves the ingestion rule:

1. receive a new logical Telegram event;
2. persist all intended RouteExecutions;
3. advance the live seen floor;
4. process each durable route independently.

Telegram/Telethon code does not own content business rules.

## Processing contracts

### RouteContentPolicy

Resolved per route:

- include keywords;
- exclude keywords;
- allowed media types;
- Telegram URL removal;
- general URL removal;
- trailing source-right removal;
- emoji preservation;
- line-break preservation;
- whitespace trimming;
- bounded blank lines;
- branding.

### ProcessedContent

Contains two text forms:

- `normalized_text` — route-normalized content intended for filtering/dedup semantics;
- `rendered_text` — publish-facing text after branding.

This separation is intentional. A destination footer/link must not make otherwise identical source content appear different to the dedup service.

### ProcessingResult

Every route yields:
- execution id;
- decision;
- reason code;
- optional processed content.

Decisions in Phase 4:
- `ready_for_dedup`;
- `filtered`;
- `failed`.

## Policy precedence

### Filter/transform

Filter and transform profiles are route-local.

When no profile is assigned, Phase 4 uses conservative defaults compatible with the rebuild contracts:
- remove URLs;
- remove trailing source rights;
- preserve emoji;
- preserve line breaks;
- trim surrounding whitespace.

### Branding

Precedence:

1. route branding profile;
2. destination branding profile;
3. no branding.

Route branding is never merged with destination branding.

## URL contract

The V2 schema has one legacy `remove_urls` flag. Phase 4 maps it to both Telegram and general URL removal by default.

Optional route filter `custom_rules` can override independently:

```json
{
  "remove_telegram_urls": true,
  "remove_general_urls": false
}
```

This makes Telegram URL behavior explicit without a schema migration.

## Source-right contract

Source/right cleaning is suffix-oriented.

Recognized trailing markers include Arabic and English source/credit conventions plus trailing Telegram credit links. Separator lines adjacent to that suffix can be removed.

Phase 4 does not search-and-delete arbitrary words such as "المصدر" from the middle of valid content.

## Arabic and emoji safety

Text normalization:
- keeps Arabic letters/numbers/punctuation;
- normalizes line endings;
- optionally trims/compacts whitespace;
- preserves emoji by default;
- when emoji removal is requested, removes known emoji/symbol ranges instead of using a broad non-Arabic filter.

## Media contract

Phase 4 classifies metadata only. It does not download Telegram media or stage files.

Normalized media descriptors expose:
- media type;
- stable identity;
- source Telegram message id;
- file name when available;
- MIME type when available;
- byte size when available.

Supported classifications:
- photo;
- video;
- document;
- audio;
- voice;
- unknown.

Albums are one logical `ContentType.ALBUM` and preserve Telegram source order.

Media download/staging/cleanup remains Phase 7.

## Filter contract

Filtering runs on normalized content before branding.

Deterministic reason codes:

| Reason | Meaning |
| --- | --- |
| `missing_include_keyword` | include list exists and no item matched |
| `matched_exclude_keyword` | an excluded term matched |
| `media_type_not_allowed` | at least one media item violates the route allow-list |
| `empty_content` | no normalized text and no media |
| `content_processing_error` | processing failed and the route was durably failed |

## RouteExecution state contract

Migration `20261008_02_content_processing_state.py` adds:

`ready_for_dedup`

Allowed relevant transitions:

```text
received -> processing
processing -> filtered
processing -> ready_for_dedup
processing -> failed

ready_for_dedup -> duplicate
ready_for_dedup -> queued
ready_for_dedup -> failed
ready_for_dedup -> cancelled
```

The downstream transitions exist to preserve a clean phase boundary; Phase 4 itself does not deduplicate or enqueue.

`ready_for_dedup` is not checkpoint-safe and is not final.

Therefore a route that still awaits dedup/downstream durability blocks `last_committed_message_id`, while a fully filtered fan-out can commit safely.

## Persistence boundary

Phase 4 intentionally does not force the existing shared `content_items` table to store route-specific transformed bodies.

The current V2 `ContentItem` uniqueness contract is source/message-oriented and would conflate separate route transformations.

For Phase 4:
- RouteExecution state is durable;
- processed body/media descriptors remain application data passed to the next stage;
- no queue or publisher is invoked.

Phase 5/6 must establish the final durable processed-content/dedup/queue handoff and restart semantics before V3 is eligible for E2E or cutover.

## Verification

GitHub Actions Run `37694872101` completed successfully for code head `621ba5e13f137bee0ac6c8b57674e2129753b31c`.

Verified:
- 24 focused V3 phase-contract tests;
- 102 non-integration tests passed, 10 deselected;
- Ruff;
- compileall;
- PostgreSQL 16 schema bootstrap;
- Alembic upgrade/downgrade/re-upgrade through `20261008_02`;
- 9 PostgreSQL V3 integration tests;
- V3 CLI;
- Docker build.

The PostgreSQL Phase 4 tests prove:
- route-profile isolation;
- route branding precedence;
- destination branding fallback;
- independent route normalization;
- persisted `ready_for_dedup`;
- checkpoint advance only when all route outcomes are checkpoint-safe.

## Explicit exclusions

Phase 4 does not implement:
- fingerprints;
- duplicate lookup;
- time-window deduplication;
- queue creation;
- scheduling;
- Bot API publishing;
- media staging;
- Control Bot V3;
- production migration;
- real Telegram pilot.

Those remain Phase 5+.

## Production safety

No Railway setting, branch, start command, volume, Telegram session, or production database migration was changed.

Production remains `main` + Runtime V2.
