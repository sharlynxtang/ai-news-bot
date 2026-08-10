"""collect.py — standalone collection stage of the AI News Bot.

Runs every collector (RSS, Hacker News, arXiv, Chinese sources), applies the
strict SGT 10:30-aligned time window (already enforced inside each collector),
de-duplicates by URL, and writes two JSON artifacts to the output directory so
a downstream Agent can make editing decisions before summarize + send:

    output/candidates.json    all deduped candidate items (enriched schema)
    output/collect_stats.json collection funnel + time-window metadata

Usage:
    python collect.py                     # write to ./output/
    python collect.py --output-dir out/   # custom output directory
    python collect.py --quiet             # suppress per-collector progress logs

Exit code 0 on success.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from src import config
from src.collectors import collect_all
from src.collectors.base import date_label_cn
from src.summarizer.llm_summarizer import SOURCE_CN


def _log(msg: str, *, quiet: bool = False) -> None:
    """Emit progress to stderr so stdout/JSON files stay clean."""
    if not quiet:
        print(msg, file=sys.stderr)


def build_candidates(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Enrich raw collector items with ``source_cn`` and ``date_label``.

    The collector schema already carries title/url/source/summary/category/
    published_at; here we add the two display fields the summarizer normally
    derives on the fly, so the persisted candidate list is self-contained.
    """
    candidates: List[Dict[str, Any]] = []
    for it in items:
        source = it.get("source", "")
        candidates.append(
            {
                "title": it.get("title", ""),
                "url": it.get("url", ""),
                "source": source,
                "source_cn": SOURCE_CN.get(source, source or "未知来源"),
                "summary": it.get("summary", ""),
                "category": it.get("category", "other"),
                "published_at": it.get("published_at", ""),
                "date_label": date_label_cn(it.get("published_at")),
            }
        )
    return candidates


def build_stats(stats: Dict[str, Any], deduped_count: int) -> Dict[str, Any]:
    """Assemble the collection-statistics payload.

    ``stats`` is the dict returned by ``collect_all``: per-collector counts
    plus reserved funnel keys (``_total_collected``, ``_deduped``,
    ``_sources``). We surface the per-source counts (dropping reserved keys),
    the funnel totals, the active SGT time window, and a collection timestamp.
    """
    sources = {
        key: count
        for key, count in stats.items()
        if not key.startswith("_")
    }

    start_utc, end_utc = config.get_time_window()
    local_tz = config._local_tz()

    return {
        "total_collected": stats.get("_total_collected", deduped_count),
        "total_deduped": stats.get("_deduped", deduped_count),
        "sources": sources,
        "time_window": {
            "start": start_utc.astimezone(local_tz).isoformat(),
            "end": end_utc.astimezone(local_tz).isoformat(),
        },
        "collected_at": datetime.now(timezone.utc)
        .astimezone(local_tz)
        .isoformat(),
    }


def run(output_dir: Path, *, quiet: bool = False) -> int:
    """Collect, enrich, and persist candidates + stats. Return exit code."""
    output_dir.mkdir(parents=True, exist_ok=True)

    items, stats = collect_all(verbose=not quiet)
    candidates = build_candidates(items)
    stats_payload = build_stats(stats, len(candidates))

    candidates_path = output_dir / "candidates.json"
    stats_path = output_dir / "collect_stats.json"

    candidates_path.write_text(
        json.dumps(candidates, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    stats_path.write_text(
        json.dumps(stats_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    _log(
        f"[collect] wrote {len(candidates)} candidates → {candidates_path}",
        quiet=quiet,
    )
    _log(f"[collect] wrote stats → {stats_path}", quiet=quiet)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Collect AI news candidates and write JSON artifacts."
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Directory for candidates.json and collect_stats.json "
        "(created if missing). Default: output/",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-collector progress logs.",
    )
    args = parser.parse_args()

    return run(Path(args.output_dir), quiet=args.quiet)


if __name__ == "__main__":
    raise SystemExit(main())
