# V3 Phase 12 — Production Cutover Checklist

Date: 2026-10-10  
Status: PREPARED / NOT AUTHORIZED FOR EXECUTION

This checklist does not authorize deployment by itself.

## Entry gates

Production cutover may start only after:
- Phase 11 real Telegram pilot is successful;
- source-to-target text/media/album evidence is recorded;
- retry/reconnect/restart behavior is accepted;
- production rollback point is known;
- current V2 deployment is healthy immediately before cutover.

## Preserve before cutover

Preserve:
- current production deployment/revision as rollback point;
- current runtime credentials in Railway;
- persistent application volume;
- authorized Telegram user-session file;
- current service build settings that remain valid for V3;
- current PostgreSQL service until the clean V3 database target is explicitly selected.

Do not preserve as V3 application input:
- legacy SQLite data;
- V1/V2 source/destination/route rows;
- queue/publication/content history;
- V2 runtime identifiers.

## Required V3 runtime state

The production V3 service must have:
- clean V3 PostgreSQL schema at current Alembic head;
- fresh Account/Project/TelegramAccount bootstrap identifiers;
- Telegram ingestion enabled;
- publisher enabled;
- Control Bot enabled;
- correct session path on persistent storage;
- production environment/logging settings;
- no automatic legacy-import command.

## Telegram session handoff

Critical invariant: one authorized user session must never run concurrently in V2 and V3.

Sequence:
1. confirm V2 healthy and record rollback deployment;
2. pause/stop V2 process ownership;
3. verify no second process owns the session;
4. keep the existing session file on persistent storage;
5. point V3 to the approved session location;
6. start exactly one V3 process;
7. confirm Telegram connection before configuring live routes.

If V3 cannot authorize/connect, stop V3 before restoring V2.

## Railway service switch

The V2 production start command currently runs legacy-import logic and Runtime V2.

For V3:
- remove the legacy-import startup wrapper;
- use the repository-defined V3 runtime entrypoint;
- retain backend root and Dockerfile build;
- retain persistent volume mount;
- set V3 runtime enablement/configuration;
- set fresh V3 internal identifiers;
- deploy one replica only while the Telegram user session is single-owner.

## Post-start validation

Before adding production routes:
- readiness is healthy;
- database readiness is healthy;
- Telegram user session reports connected;
- Control Bot responds to owner-only status;
- queue has no unexpected failed/pending jobs;
- Sources/Destinations/Routes are empty or explicitly expected.

Then add topology through Control Bot V3.

## Smoke sequence

Run with controlled test source/target first:
1. text;
2. photo;
3. album;
4. verify PublishedMessage and queue state;
5. confirm no historical replay;
6. confirm control/status diagnostics.

Only then add normal production topology.

## Rollback trigger

Rollback immediately if:
- Telegram session authorization fails persistently;
- unexpected history replay occurs;
- duplicate posts appear;
- queue loses durable state;
- control plane cannot safely pause/reload;
- database/runtime readiness is unstable.

Rollback sequence:
1. stop V3 first;
2. ensure Telegram session is no longer owned by V3;
3. restore the recorded V2 deployment/start configuration;
4. start one V2 process only;
5. verify Telegram connection and bot health;
6. investigate V3 offline.

## Cleanup after acceptance

Only after an observation window is accepted:
- make V3 branch/content the production source;
- archive/remove V1/V2 runtime entrypoints;
- remove legacy import from normal startup;
- remove obsolete V2-only variables;
- update all source-of-truth docs;
- keep historical Git tags/commits for rollback/audit.

## Non-goal

Do not delete the rollback point or old code during the first V3 startup.
