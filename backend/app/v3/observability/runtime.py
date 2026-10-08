"""Runtime lifecycle observability component."""

from __future__ import annotations

from collections.abc import Mapping

from .service import ObservabilityServiceV3


class ObservabilityRuntimeComponent:
    """Persist content-free runtime lifecycle events."""

    name = "observability"

    def __init__(self, service: ObservabilityServiceV3) -> None:
        self.service = service
        self._started = False

    async def start(self) -> None:
        self._started = True

    async def runtime_ready(self, snapshot: Mapping[str, object]) -> None:
        if not self._started:
            return
        await self.service.record_event(
            "runtime_ready",
            details={
                "state": str(snapshot.get("state", "unknown")),
                "database": bool(snapshot.get("database", False)),
                "components_started": int(snapshot.get("components_started", 0)),
            },
        )

    async def stop(self) -> None:
        if not self._started:
            return
        await self.service.record_event("runtime_stopping")
        self._started = False
