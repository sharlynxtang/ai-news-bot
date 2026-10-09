"""Collectors package.

Exposes the registry and a `collect_all` runner that fans out to every
collector with fault isolation (one failing source never aborts the rest).
"""
from __future__ import annotations

import time
from typing import Callable, Dict, List

from .arxiv_collector import ArxivCollector
from .atyun_collector import AtyunCollector
from .base import BaseCollector, NewsItem, log, sort_and_trim
from .hackernews import HackerNewsCollector
from .rss_collector import RSSCollector
from .x_collector import XCollector

# Registry of collector factories. RSS covers all configured feeds (incl. the
# Chinese sources 量子位, 雷锋网, InfoQ AI and others in config.RSS_FEEDS).
COLLECTORS: Dict[str, Callable[[], BaseCollector]] = {
    "rss": RSSCollector,
    "hackernews": HackerNewsCollector,
    "arxiv": ArxivCollector,
    "atyun": AtyunCollector,
    "x": XCollector,
}


def collect_all(verbose: bool = True) -> tuple[List[NewsItem], Dict[str, int]]:
    """Run every collector, isolating failures per collector.

    Returns the merged, de-duplicated, time-sorted item list plus a stats
    dict. The stats dict maps each collector key to the number of items it
    produced, and additionally carries pipeline-funnel metadata under
    reserved keys used by the summarizer's「筛选说明」section:

        "_total_collected" : int        # items before URL de-duplication
        "_deduped"         : int        # items after de-duplication
        "_sources"         : list[str]  # distinct source ids present in output
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

    total_collected = len(all_items)

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

    # Distinct source ids present in the final (deduped) output, in first-seen
    # order — used by the summarizer to render the data-source list.
    source_order: List[str] = []
    seen_src: set[str] = set()
    for it in deduped:
        s = it.get("source", "")
        if s and s not in seen_src:
            seen_src.add(s)
            source_order.append(s)

    stats["_total_collected"] = total_collected
    stats["_deduped"] = len(deduped)
    stats["_sources"] = source_order  # type: ignore[assignment]

    return deduped, stats


__all__ = [
    "ArxivCollector",
    "AtyunCollector",
    "HackerNewsCollector",
    "RSSCollector",
    "XCollector",
    "COLLECTORS",
    "collect_all",
]
