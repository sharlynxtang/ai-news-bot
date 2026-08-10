"""Collectors package.

Exposes the registry and a `collect_all` runner that fans out to every
collector with fault isolation (one failing source never aborts the rest).
"""
from __future__ import annotations

import time
from typing import Callable, Dict, List

from .arxiv_collector import ArxivCollector
from .base import BaseCollector, NewsItem, log, sort_and_trim
from .hackernews import HackerNewsCollector
from .rss_collector import RSSCollector

# Registry of collector factories. RSS covers all configured feeds (incl. the
# Chinese source 机器之心) so it counts as multiple logical sources.
COLLECTORS: Dict[str, Callable[[], BaseCollector]] = {
    "rss": RSSCollector,
    "hackernews": HackerNewsCollector,
    "arxiv": ArxivCollector,
}


def collect_all(verbose: bool = True) -> tuple[List[NewsItem], Dict[str, int]]:
    """Run every collector, isolating failures per collector.

    Returns the merged, de-duplicated, time-sorted item list plus a stats
    dict mapping each collector key to the number of items it produced.
    """
    all_items: List[NewsItem] = []
    stats: Dict[str, int] = {}

    for key, factory in COLLECTORS.items():
        start = time.time()
        try:
            collector = factory()
            items = collector.collect()
        except Exception as exc:  # noqa: BLE001 - hard fault isolation
            log(f"[{key}] collector crashed: {exc}")
            items = []
        stats[key] = len(items)
        all_items.extend(items)
        if verbose:
            elapsed = time.time() - start
            log(f"[{key}] done: {len(items)} items in {elapsed:.1f}s")

    # De-duplicate by URL across sources, keeping the first (already sorted).
    all_items = sort_and_trim(all_items)
    seen: set[str] = set()
    deduped: List[NewsItem] = []
    for it in all_items:
        u = it.get("url", "")
        if u and u in seen:
            continue
        seen.add(u)
        deduped.append(it)

    return deduped, stats


__all__ = [
    "ArxivCollector",
    "HackerNewsCollector",
    "RSSCollector",
    "COLLECTORS",
    "collect_all",
]
