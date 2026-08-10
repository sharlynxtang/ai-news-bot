"""arXiv collector via the arXiv Atom API."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

import feedparser

from .. import config
from .base import BaseCollector, NewsItem, in_time_window, log, sort_and_trim


class ArxivCollector(BaseCollector):
    """Fetch the newest AI papers from arXiv.

    Queries the configured categories (cs.AI, cs.CL, cs.CV, cs.LG), sorted by
    submission date descending, then keeps papers submitted within the
    lookback window. The summary is the first two sentences of the abstract.

    Note: arXiv has no citation/attention signal in its API, so "sorted by
    attention" is approximated by recency (submittedDate desc), which is the
    closest available proxy without a third-party service.
    """

    source_name = "arxiv"

    def collect(self) -> List[NewsItem]:
        # OR the categories into a single search query.
        # Use " OR " (with spaces); requests encodes spaces as "+", which is
        # what the arXiv API expects. A literal "+OR+" would be percent-encoded
        # to "%2BOR%2B" and silently return zero results.
        cat_query = " OR ".join(f"cat:{c}" for c in config.ARXIV_CATEGORIES)
        params = {
            "search_query": cat_query,
            "start": 0,
            "max_results": 100,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
        resp = self._request(config.ARXIV_API_URL, params=params)
        if resp is None:
            log("[arxiv] collected 0 items")
            return []

        parsed = feedparser.parse(resp.content)

        items: List[NewsItem] = []
        for entry in parsed.entries:
            title = " ".join(entry.get("title", "").split()).strip()
            url = entry.get("link", "").strip()
            if not title or not url:
                continue

            published_dt = self._entry_dt(entry)
            published = self._iso_utc(published_dt)
            # Strict SGT window, relaxed on the start side by the configured
            # slack so arXiv's publication lag doesn't drop otherwise-in-window
            # papers. Undated entries are dropped (a paper should always date).
            if not in_time_window(
                published,
                slack_hours=config.ARXIV_WINDOW_SLACK_HOURS,
                keep_undated=False,
            ):
                continue

            abstract = entry.get("summary", "")
            summary = self._first_sentences(abstract, max_sentences=2)

            items.append(
                self.make_item(
                    title=title,
                    url=url,
                    source=self.source_name,
                    summary=summary,
                    category="paper",
                    published_at=published,
                )
            )

        items = sort_and_trim(items, config.MAX_ITEMS_PER_SOURCE)
        log(f"[arxiv] collected {len(items)} items")
        return items

    @staticmethod
    def _entry_dt(entry) -> Optional[datetime]:
        for key in ("published_parsed", "updated_parsed"):
            parsed = entry.get(key)
            if parsed:
                try:
                    return datetime(*parsed[:6], tzinfo=timezone.utc)
                except Exception:  # noqa: BLE001
                    continue
        return None
