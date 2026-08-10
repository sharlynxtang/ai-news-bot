"""Entry point: `python -m src.collectors`.

Runs all collectors and prints the results as a JSON array plus a per-source
statistics summary (to stderr so the JSON on stdout stays clean and pipeable).
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from . import collect_all


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Collect AI news from all configured sources."
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print the JSON output (indent=2).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-collector progress logs.",
    )
    args = parser.parse_args()

    start = time.time()
    items, stats = collect_all(verbose=not args.quiet)
    elapsed = time.time() - start

    # Human-readable stats -> stderr.
    print("\n=== Collection summary ===", file=sys.stderr)
    for source, count in stats.items():
        print(f"  {source:12s}: {count}", file=sys.stderr)
    print(f"  {'total (dedup)':12s}: {len(items)}", file=sys.stderr)
    print(f"  elapsed     : {elapsed:.1f}s", file=sys.stderr)

    # Machine-readable JSON -> stdout.
    if args.pretty:
        print(json.dumps(items, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(items, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
