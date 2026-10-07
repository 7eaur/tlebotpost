#!/usr/bin/env python3
"""Run the integrated v2 runtime."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging

from app.runtime_v2 import RuntimeV2, RuntimeV2Settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Telegram Relay Runtime v2")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate module wiring without loading secrets or opening connections",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="start the listener, process one publish batch, then stop",
    )
    parser.add_argument("--no-database-check", action="store_true")
    return parser


async def run(args: argparse.Namespace) -> dict[str, object]:
    if args.dry_run:
        return {
            "mode": "dry-run",
            "runtime": "v2",
            "components": [
                "TelegramClientManager",
                "V2TelegramListener",
                "ContentPipeline",
                "PublishQueue",
                "PublishWorker",
            ],
            "external_connections": False,
        }

    settings = RuntimeV2Settings.from_env(args.env_file)
    runtime = RuntimeV2.from_env(args.env_file)
    if args.once:
        await runtime.start(
            check_database=not args.no_database_check,
            start_worker=False,
        )
        try:
            processed = await runtime.run_worker_once()
            return {"mode": "once", "runtime": "v2", "processed_jobs": processed}
        finally:
            await runtime.stop()

    await runtime.start(check_database=not args.no_database_check)
    try:
        await runtime.wait()
    finally:
        await runtime.stop()
    return {"mode": "continuous", "runtime": "v2", "worker_id": settings.worker_id}


def main() -> None:
    args = build_parser().parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        result = asyncio.run(run(args))
    except KeyboardInterrupt:
        result = {"mode": "stopped", "runtime": "v2"}
    except Exception as exc:
        raise SystemExit(f"Runtime v2 failed: {exc}") from exc
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
