#!/usr/bin/env python3
"""Run the unified Telegram Relay V2 service."""

from __future__ import annotations

import argparse
import asyncio
import logging

from dotenv import load_dotenv

from app.service import run_service


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Telegram Relay V2 unified service")
    parser.add_argument("--env-file", default=".env")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    env_file = None if args.env_file in {"", "/dev/null", "none", "None"} else args.env_file
    if env_file is not None:
        load_dotenv(env_file, override=False)
    logging.basicConfig(
        level=getattr(logging, __import__("os").getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    asyncio.run(run_service(env_file=None))


if __name__ == "__main__":
    main()
