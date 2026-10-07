"""Run the V3 application foundation with one repository-defined entrypoint."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from pathlib import Path

from .config import RuntimeV3Settings
from .runtime import RuntimeV3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Telegram Relay V3")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument(
        "--require-telegram",
        action="store_true",
        help="require Telegram credentials even before the ingestion phase is enabled",
    )
    return parser


async def run(*, env_file: str | Path | None, require_telegram: bool = False) -> None:
    settings = RuntimeV3Settings.from_env(
        env_file,
        require_telegram=require_telegram,
    )
    logging.basicConfig(
        level=getattr(logging, settings.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    runtime = RuntimeV3.from_settings(settings)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, runtime.request_stop)
        except (NotImplementedError, RuntimeError):
            pass

    try:
        await runtime.start()
        await runtime.wait()
    finally:
        await runtime.stop()


def main() -> None:
    args = build_parser().parse_args()
    try:
        asyncio.run(
            run(
                env_file=args.env_file,
                require_telegram=args.require_telegram,
            )
        )
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
