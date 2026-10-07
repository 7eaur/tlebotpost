"""Unified long-running service for Runtime v2, Control Bot, and Web API."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import uvicorn

from app.control.v2_bot import ControlBotV2
from app.runtime_v2 import RuntimeV2
from app.web import create_web_app


async def run_service(env_file: str | Path | None = ".env") -> None:
    runtime = RuntimeV2.from_env(env_file)
    logger = logging.getLogger(__name__)
    public_base_url = os.getenv("PUBLIC_BASE_URL", "").strip()
    control: ControlBotV2 | None = None

    if runtime.settings.owner_id is not None:
        control = ControlBotV2(
            token=runtime.settings.bot_token,
            owner_id=runtime.settings.owner_id,
            runtime=runtime,
            public_base_url=public_base_url,
        )

    app = create_web_app(runtime)
    port = int(os.getenv("PORT", "8080"))
    host = os.getenv("HOST", "0.0.0.0")
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level=os.getenv("LOG_LEVEL", "info").lower(),
            access_log=os.getenv("WEB_ACCESS_LOG", "false").lower() in {"1", "true", "yes"},
            proxy_headers=True,
            forwarded_allow_ips="*",
        )
    )

    try:
        if control is not None:
            await control.start()
        try:
            await runtime.start(listener_required=False)
        except Exception:
            logger.exception("Runtime v2 failed to enter degraded startup")
        logger.info(
            "Unified V2 service started: web=%s:%s control_bot=%s",
            host,
            port,
            control is not None,
        )
        await server.serve()
    finally:
        if control is not None:
            try:
                await control.stop()
            except Exception:
                logger.warning("Control bot shutdown failed", exc_info=True)
        try:
            await runtime.stop()
        except Exception:
            logger.warning("Runtime shutdown failed", exc_info=True)
