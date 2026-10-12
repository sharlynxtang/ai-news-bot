"""Safely check the one-time catch-up job's Feishu webhook setup."""

from __future__ import annotations

import os
import sys
from urllib.parse import urlsplit

import requests


def main() -> int:
    url = os.environ.get("FEISHU_WEBHOOK_URL", "")
    if url != url.strip() or any(char.isspace() for char in url):
        print("::error::FEISHU_WEBHOOK_URL contains whitespace; remove spaces and newlines")
        return 1
    if url.startswith(("'", '"')) or url.endswith(("'", '"')):
        print("::error::FEISHU_WEBHOOK_URL contains quote characters; paste the URL alone")
        return 1
    if url.startswith("FEISHU_WEBHOOK_URL="):
        print("::error::FEISHU_WEBHOOK_URL contains a variable name; paste only the URL")
        return 1
    try:
        parts = urlsplit(url)
    except ValueError:
        print("::error::FEISHU_WEBHOOK_URL is not a valid URL")
        return 1
    if parts.scheme != "https":
        print("::error::FEISHU_WEBHOOK_URL must begin with https://")
        return 1
    if parts.hostname not in ("open.feishu.cn", "open.larksuite.com"):
        print("::error::FEISHU_WEBHOOK_URL must use a Feishu or Lark API domain")
        return 1
    if not parts.path.startswith("/open-apis/bot/v2/hook/"):
        print("::error::FEISHU_WEBHOOK_URL is missing the bot/v2/hook path")
        return 1
    if len(parts.path.removeprefix("/open-apis/bot/v2/hook/")) < 8:
        print("::error::FEISHU_WEBHOOK_URL is missing the webhook token")
        return 1
    print("::notice::Feishu webhook format looks valid")

    try:
        response = requests.get(f"https://{parts.hostname}", timeout=8)
    except requests.RequestException as exc:
        print(f"::warning::Feishu domain probe failed ({type(exc).__name__})")
    else:
        print(f"::notice::Feishu domain reachable (HTTP {response.status_code})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
