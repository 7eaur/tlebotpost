# V3 Phase 7 — Publisher and Media Lifecycle

Date: 2026-10-09  
Branch: `rebuild/v3-foundation-20261007`  
Verified code head: `1c2c7f7c63e8461bc65dc3a8113122e5075cb552`  
CI run: `37848919833` — success

## Goal

Complete the queue-to-Telegram publishing path without changing production, while preserving the live-only ingestion contract and avoiding unsafe duplicate replay around external Bot API calls.

## Resulting V3 path

```text
Telegram source event
  -> durable SourceEventSnapshot
  -> RouteExecution
  -> Content Processing
  -> route-specific durable payload
  -> typed Deduplication
  -> durable PublishJob
  -> claim + lease
  -> transient source-media staging
  -> Telegram Bot API
  -> PublishedMessage / PublicationAttempt
  -> RouteExecution.published
```

## Publisher worker

`PublisherWorkerComponent` owns V3 publish execution.

It:
- claims only V3 queue rows;
- runs bounded batches;
- maintains a lease heartbeat;
- translates publisher outcomes into queue success/retry/failure;
- never consumes legacy V2 jobs.

## Runtime enablement

Publisher startup is explicit:

```text
V3_PUBLISHER_ENABLED=true
```

Required:
- normal V3 Telegram settings;
- `BOT_TOKEN`.

Publisher-specific settings include:
- worker id;
- poll interval;
- batch size;
- lease duration;
- Bot API send interval;
- media staging path;
- stale-staging retention.

If publisher mode is disabled, V3 can still boot its non-publishing foundation.

## Shared Telegram user session

The publisher does not open another Telethon session.

It reuses the same `TelethonUserAdapter` already connected by Telegram ingestion.

The media API is targeted:

```text
download_message_media(chat_id, message_id, destination_dir)
```

Only message ids already accepted into V3 are fetched.

This does not enumerate or replay channel history.

## Media lifecycle

For each publish attempt:
1. create attempt-scoped temporary directory;
2. fetch each required source message by exact id;
3. download media bytes;
4. publish through Bot API;
5. remove the attempt directory.

Stale managed directories are removed at publisher startup after a configured age.

Media bytes are not stored in PostgreSQL or Git.

## Supported message shapes

### Text

Text-only messages publish through `send_message`.

Text above Telegram's message limit is split deterministically.

No content is intentionally truncated.

### Single media

Supported:
- photo;
- video;
- document;
- audio;
- voice;
- unknown media as document fallback.

### Albums

One album remains:
- one logical RouteExecution;
- one PublishJob.

Bot-API-compatible group families:
- photo/video mixes;
- document-only;
- audio-only.

Unsupported mixes fail explicitly instead of silently changing post semantics.

## Caption fallback

Telegram media captions have a smaller limit than text messages.

If rendered text fits the caption limit:
- it is attached to the first media item.

If it does not fit:
- media is sent without caption;
- full rendered text is sent afterward using safe text chunks.

## Durable publication result

Success records:
- PublishJob -> published;
- PublicationAttempt -> succeeded;
- RouteExecution -> published;
- PublishedMessage row.

PublishedMessage metadata stores:
- all target Telegram message ids;
- message count;
- whether the result is partial;
- V3 marker.

The first target message id remains in the legacy-compatible primary field.

## Retry and error mapping

Classified outcomes include:
- Telegram RetryAfter;
- network/timeouts;
- Forbidden;
- BadRequest;
- generic Telegram errors;
- media acquisition unavailability;
- media-download temporary failures.

RetryAfter is allowed to return to queue retry when no target message has been confirmed.

## External-side-effect safety

Telegram Bot API does not provide an idempotency key.

V3 therefore distinguishes:

```text
processing  = claimed but external publishing has not started
publishing  = external publish path has started
```

### Crash while processing

Safe to retry through Phase 6 lease recovery.

### Crash while publishing

The external result may be unknown.

Expired `publishing` lease becomes:

```text
failed / publish_outcome_unknown
```

It is not automatically replayed.

### Partial publish

If one or more target message ids were confirmed before a later operation failed:
- confirmed ids are persisted;
- the job fails closed;
- the entire logical post is not automatically replayed.

This favors avoiding duplicate target posts over pretending the external API is exactly-once.

## Verification

GitHub Actions Run `37848919833` completed successfully at:

`1c2c7f7c63e8461bc65dc3a8113122e5075cb552`

Passed:
- 51 focused V3 tests;
- 129 non-integration tests, 27 deselected;
- Ruff;
- compileall;
- PostgreSQL V2-reference bootstrap;
- migration upgrade/downgrade/re-upgrade through `20261008_04`;
- 26 PostgreSQL V3 integration tests through the publisher path;
- V3 CLI;
- Docker build.

Verified publisher scenarios include:
- text success and durable publication state;
- long text chunking;
- caption overflow fallback;
- targeted media staging and cleanup;
- album-as-one-job semantics;
- RetryAfter queue integration;
- partial publish fail-closed;
- unknown publish result after expired publishing lease.

## Real Telegram evidence

Not executed.

The only authorized live user session belongs to the current Railway V2 production environment.

It was not copied, shared or run concurrently with V3.

A real source -> V3 -> target sandbox test requires a separate authorized V3 session and controlled target channel.

This remains part of the formal end-to-end pilot gate rather than being misrepresented as complete.

## Production safety

Phase 7 did not:
- deploy V3 to Railway production;
- change the production branch;
- change Railway environment variables;
- migrate production PostgreSQL;
- copy/move/share the live Telegram session;
- publish to a real production target.

Production remains Runtime V2 on `main`.
