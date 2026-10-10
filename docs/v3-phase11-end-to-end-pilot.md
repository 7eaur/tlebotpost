# V3 Phase 11 — End-to-End Pilot

Date: 2026-10-10  
Status: PRE-PILOT VERIFIED IN CI / REAL TELEGRAM EVIDENCE REQUIRED

## Goal

Prove the complete V3 critical path from a newly received Telegram source event to durable target publication, then repeat the same contract against real Telegram in an isolated sandbox.

## Pre-pilot CI path

The PostgreSQL E2E gate wires the actual V3 components together:

```text
TelegramIngestionComponent
  -> ContentProcessingCoordinator
  -> DeduplicationCoordinator
  -> PublishQueueV3
  -> TelegramPublisherV3
  -> PublishedMessage
```

Fake adapters replace only the external Telegram network.

All application/domain/database components are the production V3 implementations.

## Covered in the integrated gate

The E2E scenario verifies:
- live startup baseline;
- one source fan-out to two destinations;
- durable RouteExecution per destination;
- content processing;
- typed deduplication;
- queue creation;
- publisher claim and completion;
- durable PublishedMessage rows;
- exact same content arriving under a new Telegram message id is not republished;
- grouped photo/video album stays one logical event/job per destination;
- media is acquired by exact source message id;
- publisher targets both active destinations;
- restart/rebaseline keeps live-only behavior;
- a message older than the restart baseline is ignored;
- a new post-baseline message is accepted and published;
- SourceCheckpoint seen/committed cursors advance to the safe value.

## Failure/retry evidence reused in the same CI workflow

Existing PostgreSQL publisher/reliability integration also covers:
- Telegram RetryAfter;
- permanent bad request/permission-style failures;
- partial publish fail-closed;
- unknown publish outcome after publishing lease expiry;
- max-attempt and lease recovery;
- durable queue recovery.

These tests remain mandatory in the same workflow as the E2E gate.

## Real Telegram pilot preflight

Added:

`python scripts/pilot_preflight_v3.py`

The command validates, without printing credentials:
- database readiness;
- configured V3 Account exists;
- configured TelegramAccount belongs to that Account;
- Telegram session file exists;
- publisher is enabled;
- control surface is enabled;
- current project/source/destination/route counts.

A successful preflight is required before the external pilot starts.

## Real Telegram pilot still required

CI does not prove provider behavior.

The external gate must still prove on a separate authorized V3 session:
1. text;
2. photo;
3. video;
4. document;
5. audio/voice where available;
6. supported album;
7. multi-source;
8. multi-target;
9. duplicate behavior;
10. restart/reconnect live-only behavior;
11. target permission failure;
12. retry/FloodWait behavior.

The production V2 Telegram session must not be reused concurrently for this pilot.

## Cutover preparation

The Phase 12 checklist is prepared at:

`docs/v3-phase12-cutover-checklist.md`

It is not authorization to deploy.


## CI verification evidence

Verified code head:

`1a0008295046957bd06a55c7c810f5c90c5aeb36`

GitHub Actions:
- Run ID: `38065512375`
- Conclusion: `success`

Passed:
- focused V3 tests: 63 passed;
- full non-integration suite: 141 passed, 33 deselected;
- Ruff: all checks passed;
- compileall;
- fresh V3 database bootstrap;
- migration roundtrip;
- PostgreSQL integration including the full pre-pilot E2E gate: 32 passed;
- explicit Telegram target permission failure behavior;
- V3 CLI;
- Docker build.

The real provider gate remains intentionally open.
