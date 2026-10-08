# V3 Phase 8 — Control Bot V3

Date: 2026-10-09  
Branch: `rebuild/v3-foundation-20261007`  
Verified code head: `4c8229ad5f9372ec277841f0c295b5922ebd000a`  
CI run: `37860895897` — success

## Goal

Make routine V3 operation manageable without manual PostgreSQL edits while keeping Telegram commands as a thin, owner-only interface over application services.

## Architecture

```text
Owner Telegram private chat
        |
        v
ControlBotV3
        |
        v
ControlServiceV3
        |
        +--> PostgreSQL domain models
        +--> PublishQueueV3
        +--> Telegram chat resolver
        +--> safe ingestion reload
```

The bot does not contain source/destination/route business rules.

## Authorization

A command is accepted only when:
- the Telegram sender id equals the configured owner id;
- the effective chat is private.

All command handlers perform this check before querying or mutating control data.

## Runtime configuration

Enable:

```text
V3_CONTROL_ENABLED=true
```

Owner:
- `V3_CONTROL_OWNER_ID`;
- fallback: `OWNER_ID`.

Bot token:
- `V3_CONTROL_BOT_TOKEN`;
- fallback: `BOT_TOKEN`.

Control enablement also activates the V3 Telegram user-session group because chat resolution and safe subscription reload require the authorized Telethon client.

## Shared Telegram resolution

The common Telegram reference resolver now lives under:

`app.telegram.chat_resolver`

Supported reference shapes include:
- `@username`;
- `https://t.me/username`;
- invite links;
- numeric/internal references supported by Telethon.

The legacy control resolver remains backward-compatible by re-exporting the shared implementation.

## Source creation

`/addsource`:
1. resolves the chat with the connected user session;
2. reads the latest Telegram message id;
3. inserts/updates Source;
4. persists SourceCheckpoint last_seen to that current latest id;
5. commits;
6. reloads subscriptions without rebaseline/history replay.

The first accepted event is therefore a genuinely new event after source addition.

## Destination and route creation

`/adddestination` resolves Telegram metadata and attaches the destination to:
- an explicitly selected Project; or
- the only unambiguous project when selection is unnecessary.

`/addroute` validates account-scoped Source and Destination and creates/reactivates the SourceRoute.

## Pause/resume model

Project pause/run is the broad operator switch.

Independent controls remain available for:
- Source;
- Destination;
- SourceRoute.

This prevents a broad resume from destroying intentional per-route pause state.

## Safe reload

After runtime-affecting mutations V3 calls:

`TelegramIngestionComponent.reload(rebaseline=False)`

The ingestion component:
- pauses acceptance;
- flushes pending albums;
- unsubscribes old handlers;
- reloads active topology;
- restores seen floors from SourceCheckpoint;
- subscribes the current active topology.

Configuration persistence and runtime reload are deliberately distinguishable. A saved change is not rolled back merely because live reload failed.

## Manual jobs

`/release JOB_UUID` delegates to `PublishQueueV3.release_manual`.

The control surface does not bypass queue ownership or publication rules.

## Status and health

`/status` / `/health` expose IDs/status/reason-level operational information:
- Telegram account connection/status;
- active/total projects;
- active/total sources;
- active/total destinations;
- active/total routes;
- pending V3 queue count;
- failed V3 queue count;
- Telegram last error code;
- latest publish error code.

Message bodies and credentials are not returned.

## Commands

Read:
- `/status`, `/health`;
- `/projects`;
- `/sources`;
- `/destinations`;
- `/routes`.

Mutate:
- `/addsource`;
- `/source_on`, `/source_off`;
- `/adddestination`;
- `/destination_on`, `/destination_off`;
- `/addroute`;
- `/route_on`, `/route_off`;
- `/run`, `/pause`;
- `/release`;
- `/reload`.

## Verification

GitHub Actions Run `37860895897` completed successfully at:

`4c8229ad5f9372ec277841f0c295b5922ebd000a`

Passed:
- 58 focused V3 tests;
- 136 non-integration tests, 29 deselected;
- Ruff;
- compileall;
- PostgreSQL V2-reference bootstrap;
- migration upgrade/downgrade/re-upgrade through `20261008_04`;
- 28 PostgreSQL integration tests through the control plane;
- V3 CLI;
- Docker build.

## Acceptance evidence

Verified:
- non-owner requests never reach control service status;
- owner requests outside private chat are denied;
- source creation stores the current live baseline;
- source/destination/route creation is account scoped;
- project pause/run is reversible without changing route-specific states;
- individual source/destination/route state changes persist;
- each runtime-affecting mutation requests safe reload;
- manual queue jobs can be released;
- routine topology management needs no direct SQL.

## Explicit exclusions

Phase 8 does not provide:
- OTP/password Telegram session enrollment;
- arbitrary role-based multi-user administration;
- destructive deletion flows;
- production V3 cutover.

Owner control is intentionally narrow for the current single-operator deployment model.

## Production safety

No Railway production settings, branch, deployment, volume or Telegram session were changed.

Production remains Runtime V2 on `main`.
