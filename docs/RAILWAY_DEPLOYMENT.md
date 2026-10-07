# Railway Deployment Record

## Current production target

- Repository: `7eaur/tlebotpost`
- Railway project: `authentic-ambition`
- Service: `tlebotpost`
- Environment: `production`
- Source branch: `main`
- Root directory: `/backend`
- Dockerfile: `/backend/Dockerfile`
- Runtime: V2
- Persistent volume: `tlebotpost-data` mounted at `/app/data` (500 MB)
- PostgreSQL: Railway PostgreSQL service in the same project
- Public HTTP domain: not required; the relay runs as a long-lived worker

## Runtime command

Production starts V2 with a runtime-time legacy configuration import so the mounted volume is available before the importer runs:

```sh
sh -c 'if [ -f /app/data/relay.sqlite3 ]; then python scripts/import_legacy.py --sqlite /app/data/relay.sqlite3 --account-slug runtime-v2 --project-slug runtime-v2; else echo "Legacy SQLite not found on mounted volume"; fi; exec python scripts/run_v2.py --env-file /dev/null'
```

The Telegram user session is reused from the persistent V1 session path on the volume. Secret values are stored only in Railway variables and are intentionally not documented here.

## Required production variables

- `API_ID`
- `API_HASH`
- `BOT_TOKEN`
- `OWNER_ID`
- `DATABASE_URL`
- `V2_ACCOUNT_ID`
- `V2_SESSION_PATH`
- `V2_WORKER_ID`

Branding variables may also remain configured for legacy compatibility.

## V2 migration and deployment fixes

The production rollout exposed and fixed the following issues:

1. Packaged `backend/db/schema.sql` in the Docker image.
2. Reused the already-authorized persistent Telegram session.
3. Aligned SQLAlchemy PostgreSQL enum values with the lowercase enum values created by `schema.sql`.
4. Fixed the PostgreSQL enum helper syntax.
5. Bound legacy import to the configured `V2_ACCOUNT_ID`.
6. Made repeated legacy imports synchronize existing configuration rather than only create missing rows.
7. Restored the previously disabled global relay state so the two configured legacy sources and routes are active.
8. Bound `PublicationAttempt.status` explicitly to PostgreSQL `attempt_status`.
9. Added a V2 owner startup notification after successful listener/worker startup.
10. Suppressed `httpx`/`httpcore` INFO request logging so Bot API request URLs are not written to runtime logs.

Relevant commits:

```text
bf46ff4 fix: package v2 postgres schema for deployment
b59d9a3 fix: persist postgres enum values consistently
138c5cf fix: correct pg enum helper syntax
e87866a fix: bind legacy import to configured v2 account
946a6e1 fix: correct deterministic legacy import script
ae57431 fix: synchronize legacy config on repeated imports
9221c71 feat: notify owner when runtime v2 starts
252ce52 fix: bind publication attempt enum to postgres schema
ece9fe4 fix: suppress sensitive http client request logs
```

## Production verification — 2026-10-07

Verified runtime deployment:

- Deployment: `b5a87e15-52f9-4373-a508-b94a96e40f81`
- Commit: `ece9fe41a1a2a3a35bae384ccfcc503dfade597f`
- Status: `SUCCESS`
- Replica state: one running replica, zero crashed replicas
- Persistent volume mounted successfully
- PostgreSQL connection succeeded
- Telegram user session connected successfully
- Legacy configuration import completed with:
  - 2 sources
  - 2 routes
  - 1 destination
- Listener baselines were set for both sources.
- One handler was registered for each source.
- Runtime log confirmed `v2 Telegram listener started: sources=2`.
- Runtime log confirmed `Runtime v2 startup notification sent`.
- Runtime log confirmed `Runtime v2 started`.
- No `attemptstatus`/publication-attempt enum error remained after the enum fix.
- No publish-worker cycle error was present in the verified deployment.
- Bot API request URLs were no longer emitted by HTTP client INFO logging.

## Live-only behavior

V2 rebaselines each configured source to the latest Telegram message at startup. Historical messages are not replayed. Only messages received after the current baseline enter the V2 ingestion pipeline.

## Security

- Do not commit API credentials, bot tokens, Telegram sessions, or database URLs.
- Secrets remain in Railway environment variables.
- Runtime HTTP client request logging is restricted to avoid leaking credential-bearing URLs.
- Rotate credentials whenever exposure is suspected or as part of normal operational hygiene.

## Operational checks

When diagnosing production, verify in order:

1. Railway deployment is `SUCCESS`.
2. The volume is mounted at `/app/data`.
3. Legacy import reports the expected source/route counts.
4. Telethon connects successfully.
5. Listener reports `sources=2` (or the currently expected configured count).
6. Startup notification is sent.
7. `Runtime v2 started` is present.
8. No restart loop or `publish worker cycle failed` error appears.
9. For end-to-end publication verification, use a new post in a configured source; startup intentionally does not replay historical content.
