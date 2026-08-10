"""AI News Bot — top-level orchestrator.

Pipeline: collect -> summarize -> (push).

Usage:
    python main.py --dry-run          # collect + summarize (no API call, no push), print Markdown
    python main.py --dry-run --quiet  # same, suppress per-collector progress logs
    python main.py --json             # collect only, print raw JSON (no summarizer)
    python main.py --json --pretty    # collect only, pretty-print JSON
    python main.py --test-webhook     # send a Feishu connectivity-test message and exit
    python main.py                    # full pipeline: collect + summarize (LLM) + Feishu push
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from src.collectors import collect_all
from src.pusher import FeishuPusher
from src.summarizer import LLMSummarizer


def _print_stats(stats, total, elapsed) -> None:
    print("\n=== Collection summary ===", file=sys.stderr)
    for source, count in stats.items():
        print(f"  {source:12s}: {count}", file=sys.stderr)
    print(f"  {'total (dedup)':12s}: {total}", file=sys.stderr)
    print(f"  elapsed     : {elapsed:.1f}s", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description="AI News Bot")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Run the full pipeline (collect + summarize) without calling the LLM API. "
            "Uses template formatting instead. Outputs Markdown to stdout."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Collect only and print the raw JSON news list to stdout (skips summarizer).",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="With --json: indent the JSON output.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-collector progress logs.",
    )
    parser.add_argument(
        "--test-webhook",
        action="store_true",
        help=(
            "Send a simple connectivity-test message to the Feishu webhook "
            "and exit. Verifies FEISHU_WEBHOOK_URL is reachable and valid."
        ),
    )
    args = parser.parse_args()

    # ── Webhook connectivity test (standalone, no collection) ───────────────
    if args.test_webhook:
        pusher = FeishuPusher()
        ok = pusher.test_connection()
        print(
            f"[test-webhook] {'OK' if ok else 'FAILED'}",
            file=sys.stderr,
        )
        return 0 if ok else 1

    # ── Collection ──────────────────────────────────────────────────────────
    start = time.time()
    items, stats = collect_all(verbose=not args.quiet)
    elapsed = time.time() - start
    _print_stats(stats, len(items), elapsed)

    # ── JSON-only mode (collection only, no summarizer) ─────────────────────
    if args.json:
        dump = json.dumps(
            items, ensure_ascii=False, indent=2 if args.pretty else None
        )
        print(dump)
        return 0

    # ── Summarize ───────────────────────────────────────────────────────────
    # --dry-run  → dry_run=True  (template fallback, no API call)
    # no flags   → dry_run=False (real LLM call; auto-falls back on API error)
    summarizer = LLMSummarizer(dry_run=args.dry_run)
    print("[summarizer] processing…", file=sys.stderr)
    digest = summarizer.summarize(items)
    print(digest)

    # ── Push ─────────────────────────────────────────────────────────────────
    # In --dry-run we neither call the LLM nor send to Feishu: the Markdown
    # above is the only output. A real run pushes the digest as a card.
    if args.dry_run:
        print("[pusher] dry-run: skipping Feishu push", file=sys.stderr)
        return 0

    pusher = FeishuPusher()
    print("[pusher] pushing digest to Feishu…", file=sys.stderr)
    ok = pusher.push(digest)
    if not ok:
        print("[pusher] push failed (see logs above)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
