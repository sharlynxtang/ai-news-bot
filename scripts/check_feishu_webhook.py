"""Safely check the one-time catch-up job's Feishu webhook setup."""

from __future__ import annotations

import os
import sys
from urllib.parse import urlsplit

import requests


def main() -> int:
    url = os.environ.get("FEISHU_WEBHOOK_URL", "")
    try:
        parts = urlsplit(url)
    except ValueError:
        print("::error::FEISHU_WEBHOOK_URL is not a valid URL")
        return 1
    if (
        parts.scheme != "https"
        or parts.hostname != "open.feishu.cn"
        or not parts.path.startswith("/open-apis/bot/v2/hook/")
        or len(parts.path.removeprefix("/open-apis/bot/v2/hook/")) < 8
        or any(char.isspace() for char in url)
    ):
        print(
            "::error::FEISHU_WEBHOOK_URL must be the complete "
            "https://open.feishu.cn/open-apis/bot/v2/hook/... address"
        )
        return 1
    print("::notice::Feishu webhook format looks valid")

    try:
        response = requests.get("https://open.feishu.cn", timeout=8)
    except requests.RequestException as exc:
        print(f"::warning::Feishu domain probe failed ({type(exc).__name__})")
    else:
        print(f"::notice::Feishu domain reachable (HTTP {response.status_code})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
