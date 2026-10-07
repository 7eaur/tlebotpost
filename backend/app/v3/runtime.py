"""Lifecycle orchestration for the V3 modular monolith."""

from __future__ import annotations

import asyncio
import logging
from enum import StrEnum
from typing import Protocol

from app.db import Database

from .config import RuntimeV3Settings


class RuntimeComponent(Protocol):
    """A V3 component managed by the application lifecycle."""

    name: str

    async def start(self) -> None: ...

    async def stop(self) -> None: ...


class RuntimeState(StrEnum):
    STOPPED = "stopped"
    STARTING = "starting"
    READY = "ready"
    STOPPING = "stopping"
    FAILED = "failed"


class RuntimeV3:
    """Own application startup/shutdown and dependency readiness."""

    def __init__(
        self,
        *,
        database: Database,
        settings: RuntimeV3Settings,
        components: tuple[RuntimeComponent, ...] = (),
    ) -> None:
        self.database = database
        self.settings = settings
        self.components = components
        self.state = RuntimeState.STOPPED
        self._started_components: list[RuntimeComponent] = []
        self._stop_event = asyncio.Event()
        self._logger = logging.getLogger(__name__)

    @classmethod
    def from_settings(cls, settings: RuntimeV3Settings) -> RuntimeV3:
        return cls(database=Database(settings.database), settings=settings)

    async def start(self) -> None:
        if self.state is RuntimeState.READY:
            return
        if self.state is not RuntimeState.STOPPED:
            raise RuntimeError(f"cannot start runtime from state {self.state}")

        self.state = RuntimeState.STARTING
        self._stop_event.clear()
        try:
            if not await self.database.ping():
                raise RuntimeError("PostgreSQL readiness check failed")
            for component in self.components:
                await component.start()
                self._started_components.append(component)
        except Exception:
            self.state = RuntimeState.FAILED
            await self._rollback_started_components()
            raise

        self.state = RuntimeState.READY
        self._logger.info(
            "Runtime V3 foundation ready: environment=%s components=%s",
            self.settings.environment,
            len(self._started_components),
        )

    async def stop(self) -> None:
        if self.state is RuntimeState.STOPPED:
            return
        self.state = RuntimeState.STOPPING
        self._stop_event.set()
        await self._rollback_started_components()
        await self.database.close()
        self.state = RuntimeState.STOPPED
        self._logger.info("Runtime V3 stopped")

    def request_stop(self) -> None:
        self._stop_event.set()

    async def wait(self) -> None:
        await self._stop_event.wait()

    async def readiness(self) -> dict[str, object]:
        database_ready = False
        if self.state is RuntimeState.READY:
            try:
                database_ready = await self.database.ping()
            except Exception:
                database_ready = False
        return {
            "state": self.state.value,
            "database": database_ready,
            "components_started": len(self._started_components),
        }

    async def _rollback_started_components(self) -> None:
        while self._started_components:
            component = self._started_components.pop()
            try:
                await component.stop()
            except Exception:
                self._logger.exception("V3 component stop failed: %s", component.name)
