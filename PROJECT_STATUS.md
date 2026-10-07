# Project Status — Telegram Relay Rebuild

Date: 2026-10-07
Rebuild branch: rebuild/v3-foundation-20261007
Production baseline: main@0990fb62b97c09a3fa44e41fe3c087bb5d3fd2cf

## Current phase

Phase 0 — Freeze, Audit, Contracts

Status: COMPLETE FOR REVIEW

## Production state

- Railway production remains on main.
- No production behavior was changed by the rebuild work.
- Runtime V2 is live and receives source events.
- Current blocker observed in production: false duplicate decisions caused by deduplication semantics.

## Confirmed audit findings

- V1 and V2 coexist.
- repository default entrypoint and Railway production entrypoint do not match.
- automatic legacy import still runs at production startup.
- media/album V2 path is incomplete end-to-end.
- control plane remains split between V1-oriented bot code and V2 runtime.
- no real Telegram end-to-end publication suite exists.
- deduplication can hash empty media/text into constant duplicate signals.

## Source of truth for rebuild

- docs/REBUILD_MASTER_PLAN.md
- this PROJECT_STATUS.md
- main remains the production baseline until a controlled V3 cutover.

## Next phase

Phase 1 — Runtime Foundation

Planned gate:
- single V3 entrypoint;
- centralized settings/lifecycle;
- explicit DB migration framework;
- Docker/Compose/runtime command alignment;
- no production cutover;
- tests prove the V3 skeleton can boot safely.

Do not merge or switch production before the phase gate is verified.