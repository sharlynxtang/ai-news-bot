"""send.py — standalone send stage of the AI News Bot.

Reads a Markdown digest file, converts it into a Feishu interactive message
card (reusing ``src.pusher.feishu.build_card`` via ``FeishuPusher``), and posts
it to a Feishu custom-bot webhook.

Usage:
    python send.py digest.md                 # send to the configured webhook
    python send.py digest.md --dry-run       # print the card JSON, do not send
    python send.py digest.md --webhook URL   # override the webhook URL

Exit code 0 on successful send (or successful dry-run print), 1 otherwise.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src import config
from src.pusher import FeishuPusher


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


def run(
    markdown_file: Path,
    *,
    dry_run: bool = False,
    webhook: str | None = None,
) -> int:
    """Read *markdown_file* and push it as a Feishu card. Return exit code."""
    if not markdown_file.is_file():
        _log(f"[send] file not found: {markdown_file}")
        return 1

    markdown = markdown_file.read_text(encoding="utf-8")
    if not markdown.strip():
        _log(f"[send] file is empty: {markdown_file}")
        return 1

    # Resolve the effective webhook: CLI override > config/.env default.
    effective_webhook = webhook or config.FEISHU_WEBHOOK_URL

    # A real send needs a usable webhook; a dry-run does not touch the network.
    if not dry_run:
        url = (effective_webhook or "").strip()
        if not url or "your-token" in url:
            _log(
                "[send] no valid webhook configured. Set FEISHU_WEBHOOK_URL "
                "in .env / environment or pass --webhook URL."
            )
            return 1

    pusher = FeishuPusher(webhook_url=effective_webhook, dry_run=dry_run)
    ok = pusher.push(markdown)

    if not ok:
        _log("[send] push failed (see logs above)")
        return 1

    if not dry_run:
        _log("[send] digest delivered to Feishu")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Send a Markdown digest to Feishu as an interactive card."
    )
    parser.add_argument(
        "markdown_file",
        help="Path to the Markdown digest file to send.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the Feishu card JSON instead of sending it.",
    )
    parser.add_argument(
        "--webhook",
        default=None,
        help="Override the Feishu webhook URL (defaults to FEISHU_WEBHOOK_URL).",
    )
    args = parser.parse_args()

    return run(
        Path(args.markdown_file),
        dry_run=args.dry_run,
        webhook=args.webhook,
    )


if __name__ == "__main__":
    raise SystemExit(main())
