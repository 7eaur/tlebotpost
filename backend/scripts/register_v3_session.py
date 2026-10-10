"""Interactively create a separate authorized Telegram user session for V3 pilot use."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
from pathlib import Path

from app.telegram.session import (
    TelegramSession,
    TelegramSessionPasswordNeeded,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Authorize an isolated V3 Telegram session")
    parser.add_argument(
        "--session-path",
        default=os.getenv("V3_SESSION_PATH", "data/v3-pilot.session"),
    )
    return parser


async def run(session_path: str) -> None:
    api_id_raw = os.getenv("API_ID", "").strip()
    api_hash = os.getenv("API_HASH", "").strip()
    if not api_id_raw or not api_hash:
        raise RuntimeError("API_ID and API_HASH must already exist in the environment")
    api_id = int(api_id_raw)
    if api_id <= 0:
        raise RuntimeError("API_ID must be positive")

    target = Path(session_path)
    if target.exists():
        raise RuntimeError(
            "session file already exists; choose a new isolated V3 session path"
        )

    phone = input("Telegram phone in international format: ").strip()
    session = TelegramSession(
        api_id=api_id,
        api_hash=api_hash,
        session_path=target,
    )
    try:
        request = await session.request_login_code(phone)
        code = getpass.getpass("Telegram login code: ").strip()
        try:
            await session.complete_login(
                phone=phone,
                code=code,
                phone_code_hash=request.phone_code_hash,
            )
        except TelegramSessionPasswordNeeded:
            password = getpass.getpass("Telegram two-step password: ")
            await session.complete_login_password(password=password)
    finally:
        await session.disconnect()

    if not target.is_file():
        raise RuntimeError("Telegram session authorization completed without a session file")
    os.chmod(target, 0o600)
    print(f"V3 session ready at: {target}")


def main() -> None:
    args = build_parser().parse_args()
    asyncio.run(run(args.session_path))


if __name__ == "__main__":
    main()
