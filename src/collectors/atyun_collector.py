"""AI科技评论 (atyun.com) web scraper collector.

The site has no public RSS feed; we scrape the home page and extract article
links by their monotonically-increasing numeric IDs (higher ID ≈ more recent).

Only articles with IDs above MIN_ARTICLE_ID are considered recent, which acts
as a rolling "published within the past N days" window without needing to fetch
individual article pages (which would be prohibitively slow).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import List

from bs4 import BeautifulSoup

from .. import config
from .base import BaseCollector, NewsItem, guess_category, is_ai_related, log, sort_and_trim

# Articles with IDs below this are considered too old to include.
# atyun publishes ~10-30 articles per day; 79000 is a conservative floor
# that keeps roughly the past week's output in scope.
MIN_ARTICLE_ID = 79_000

_ARTICLE_URL_RE = re.compile(r"atyun\.com/(\d+)\.html")

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class AtyunCollector(BaseCollector):
    """Scrape AI科技评论 (atyun.com) for recent AI articles.

    The home page serves static HTML listing article links; no JavaScript
    rendering required. We sort by article ID (descending) and take the top
    MAX_ITEMS_PER_SOURCE items that pass the AI-relevance filter.
    """

    source_name = "atyun"
    HOME_URL = "https://www.atyun.com/"

    def __init__(self, session=None) -> None:
        super().__init__(session=session)
        # atyun.com rejects generic bot UAs; a browser string works fine
        self.session.headers["User-Agent"] = _BROWSER_UA

    def collect(self) -> List[NewsItem]:
        resp = self._request(self.HOME_URL, timeout=15)
        if resp is None:
            log("[atyun] failed to fetch home page")
            return []

        soup = BeautifulSoup(resp.content, "html.parser")
        links = soup.find_all("a", href=_ARTICLE_URL_RE)

        # Deduplicate by article ID; keep only IDs above the recency floor
        by_id: dict[int, tuple[str, str]] = {}
        for a in links:
            m = _ARTICLE_URL_RE.search(a.get("href", ""))
            if not m:
                continue
            aid = int(m.group(1))
            if aid < MIN_ARTICLE_ID:
                continue
            title = a.get_text(strip=True)
            if not title:
                continue
            href = a["href"]
            if aid not in by_id:
                by_id[aid] = (href, title)

        if not by_id:
            log("[atyun] no recent articles found (all IDs below threshold)")
            return []

        # All atyun items share a single "now" timestamp because the listing
        # page exposes no per-article dates.  Relative order is preserved by
        # sorting on article ID before building items.
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        items: List[NewsItem] = []

        for _aid, (url, title) in sorted(by_id.items(), reverse=True):
            if not is_ai_related(title):
                continue
            category = guess_category(title, "", "product")
            items.append(
                self.make_item(
                    title=title,
                    url=url,
                    source=self.source_name,
                    summary="",
                    category=category,
                    published_at=now_str,
                )
            )

        log(f"[atyun] collected {len(items)} items")
        return sort_and_trim(items, config.MAX_ITEMS_PER_SOURCE)
