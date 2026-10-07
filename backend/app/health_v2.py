"""HTTP liveness probe for the unified Runtime v2 service."""

from __future__ import annotations

import os
import sys
import urllib.request


def check() -> None:
    port = int(os.getenv("PORT", "8080"))
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/livez", timeout=5) as response:
        if response.status != 200:
            raise RuntimeError(f"unexpected liveness status: {response.status}")


if __name__ == "__main__":
    try:
        check()
    except Exception as exc:
        print(f"healthcheck failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
