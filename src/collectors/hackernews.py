"""Hacker News collector via the Algolia HN Search API."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import List

from .. import config
from .base import (
    BaseCollector,
    NewsItem,
    guess_category,
    in_time_window,
    log,
    sort_and_trim,
)


class HackerNewsCollector(BaseCollector):
    """Search recent, high-signal AI stories on Hacker News.

    Uses the Algolia `search_by_date` endpoint with numeric filters to only
    return stories from the past N hours with at least `HN_MIN_POINTS` points.
    Results across the different query terms are de-duplicated by object id.
    """

    source_name = "hackernews"
    # search_by_date gives us a time-ordered feed we can window precisely.
    _api_url = config.HN_API_URL.replace("/search", "/search_by_date")

    def collect(self) -> List[NewsItem]:
        # Bound the API query to the strict SGT window's start so we don't pull
        # stories older than the window. The window's upper bound is enforced
        # client-side via in_time_window() below.
        window_start, _window_end = config.get_time_window()
        cutoff_ts = int(window_start.timestamp())

        seen_ids: set[str] = set()
        items: List[NewsItem] = []

        for query in config.HN_QUERIES:
            params = {
                "query": query,
                "tags": "story",
                "numericFilters": (
                    f"created_at_i>{cutoff_ts},"
                    f"points>={config.HN_MIN_POINTS}"
                ),
                "hitsPerPage": 50,
            }
            resp = self._request(self._api_url, params=params)
            if resp is None:
                continue
            try:
                data = resp.json()
            except ValueError:
                continue

            for hit in data.get("hits", []):
                obj_id = str(hit.get("objectID", ""))
                if not obj_id or obj_id in seen_ids:
                    continue
                seen_ids.add(obj_id)

                title = (hit.get("title") or "").strip()
                if not title:
                    continue
                url = hit.get("url") or (
                    f"https://news.ycombinator.com/item?id={obj_id}"
                )
                points = hit.get("points", 0)
                num_comments = hit.get("num_comments", 0)
                summary = (
                    f"{points} points, {num_comments} comments on Hacker News."
                )

                created_i = hit.get("created_at_i")
                if created_i:
                    published = self._iso_utc(
                        datetime.fromtimestamp(created_i, tz=timezone.utc)
                    )
                else:
                    published = self._iso_utc(None)

                # Enforce the window's upper bound (start is bounded by the API
                # numericFilters above). Undated hits fall back to "now" which
                # is inside the window by construction.
                if not in_time_window(published, keep_undated=True):
                    continue

                items.append(
                    self.make_item(
                        title=title,
                        url=url,
                        source=self.source_name,
                        summary=summary,
                        category=guess_category(title, "", "product"),
                        published_at=published,
                    )
                )

            # Be polite to the API between query terms.
            time.sleep(0.1)

        items = sort_and_trim(items, config.MAX_ITEMS_PER_SOURCE)
        log(f"[hackernews] collected {len(items)} items")
        return items
