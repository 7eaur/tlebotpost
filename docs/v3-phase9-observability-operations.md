# V3 Phase 9 — Observability and Operations

Date: 2026-10-09  
Branch: `rebuild/v3-foundation-20261007`  
Verified code head: `f3f11a1986b1d6d9713f9705a87db7c7e5673a89`  
CI run: `37861627737` — success

## Goal

Make V3 diagnosable from IDs, statuses and reason codes while preventing operational tooling from becoming a second path for message-content or credential leakage.

## Structured logging

V3 now configures logging through `configure_v3_logging()`.

Supported formats:
- `json` — default;
- `text` — operator-friendly fallback.

Configure with:

```text
V3_LOG_FORMAT=json
```

### JSON allow list

The structured formatter emits only:
- `timestamp`;
- `level`;
- `logger`;
- `message`;
- optional `exception_type`;
- optional `exception_message`.

It never serializes the complete LogRecord dictionary or arbitrary extras.

Full tracebacks are intentionally not emitted by the safe formatter because arbitrary exception frames/arguments may contain content or credentials.

### Redaction

Known configured secrets are replaced with:

`[REDACTED]`

Inputs include:
- complete application database URL;
- database URL password;
- Telegram API hash;
- V3 publisher Bot token;
- V3 control Bot token.

## Job diagnostics

`ObservabilityServiceV3.diagnose_job()` is account-scoped and returns:
- job id/status;
- route execution id/status/reason;
- destination/source-route ids;
- attempt/max-attempt counts;
- last error code;
- worker lock id;
- scheduling/publish times;
- Telegram target message ids;
- attempt number/status/error code/message id/latency.

It does not load:
- RoutePublishPayload body;
- normalized/rendered text;
- captions;
- PublishJob last_error_message;
- PublicationAttempt error_message.

## Metrics

`ObservabilityServiceV3.metrics()` returns:
- V3 PublishJobs grouped by status;
- RouteExecutions grouped by status;
- total V3 PublicationAttempts;
- total account SystemEvents.

Control Bot surfaces:
- `/metrics`;
- `/job JOB_UUID`.

## SystemEvent safety

`record_event()` accepts only simple primitive detail values or UUIDs.

Keys associated with secrets/session/content are rejected.

Nested arbitrary objects are rejected.

This keeps the operational event table suitable for diagnosis without turning it into message-content storage.

## Runtime lifecycle

`ObservabilityRuntimeComponent` is started before the other account-scoped V3 components so it stops last while PostgreSQL is still available.

Lifecycle records:
- `runtime_ready`;
- `runtime_stopping`.

Runtime-ready hooks execute after every configured component has started successfully.

A failed readiness hook is logged but does not fail the already-ready runtime.

## Owner readiness notification

Control Bot implements the runtime-ready hook.

The notification contains only:
- runtime state;
- database readiness;
- component count;
- active/total source and route counts;
- pending/failed queue counts.

No message body, API credential or session material is sent.

## Verification

GitHub Actions Run `37861627737` completed successfully at:

`f3f11a1986b1d6d9713f9705a87db7c7e5673a89`

Passed:
- 63 focused V3 tests;
- 141 non-integration tests, 30 deselected;
- Ruff;
- compileall;
- PostgreSQL V2-reference bootstrap;
- migration upgrade/downgrade/re-upgrade through `20261008_04`;
- 29 PostgreSQL integration tests through observability;
- V3 CLI;
- Docker build.

## Security acceptance evidence

The tests explicitly verify:
- configured secrets disappear from log output;
- exception messages are redacted;
- arbitrary LogRecord extras are omitted;
- a persisted RoutePublishPayload containing a sentinel secret message body cannot appear in JobDiagnostic;
- persisted detailed error strings cannot appear in JobDiagnostic;
- unsafe SystemEvent key names are rejected;
- nested arbitrary event payloads are rejected.

## Operations

See:

`docs/v3-operations-runbook.md`

## Production safety

No Railway deployment, production migration, backup/restore, Telegram session copy or real target publication was performed.

Production remains Runtime V2 on `main`.
