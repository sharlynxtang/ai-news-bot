"""RSS collector: generic, config-driven parsing of multiple RSS/Atom feeds."""
from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from typing import List, Optional
from urllib.parse import urljoin, urlparse

import feedparser
from bs4 import BeautifulSoup

from .. import config
from .base import (
    BaseCollector,
    NewsItem,
    guess_category,
    in_time_window,
    is_ai_related,
    log,
    sort_and_trim,
)

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    if not text:
        return ""
    return html.unescape(_TAG_RE.sub(" ", text))


def _entry_datetime(entry) -> Optional[datetime]:
    for key in ("published_parsed", "updated_parsed"):
        parsed = entry.get(key)
        if parsed:
            try:
                return datetime(*parsed[:6], tzinfo=timezone.utc)
            except Exception:  # noqa: BLE001
                continue
    return None


def _entry_image_url(entry, feed_url: str) -> str:
    """Prefer explicit feed media, then the first image in the entry body."""
    candidates = []
    for key in ("media_content", "media_thumbnail", "enclosures"):
        for media in entry.get(key, []):
            if (media.get("type") or "image/unknown").startswith("image/"):
                candidates.append(media.get("url", ""))
    for key in ("summary", "content"):
        value = entry.get(key, "")
        if isinstance(value, list):
            value = " ".join(part.get("value", "") for part in value)
        if value:
            img = BeautifulSoup(value, "html.parser").find("img", src=True)
            if img:
                candidates.append(img["src"])
    for candidate in candidates:
        url = urljoin(feed_url, candidate)
        parsed = urlparse(url)
        if parsed.scheme == "https" and parsed.hostname and not parsed.username:
            return url
    return ""


class RSSCollector(BaseCollector):
    """Parse a list of RSS feeds defined in config.RSS_FEEDS.

    Each feed keeps its own source id so downstream consumers can tell
    TechCrunch apart from The Verge. Feeds are fetched via the shared retry
    HTTP helper (so timeouts/retries are honored) and then parsed by
    feedparser from the raw bytes.
    """

    source_name = "rss"

    def __init__(self, feeds=None, session=None) -> None:
        super().__init__(session=session)
        self.feeds = feeds if feeds is not None else config.RSS_FEEDS

    def collect(self) -> List[NewsItem]:
        all_items: List[NewsItem] = []
        for feed in self.feeds:
            try:
                items = self._collect_feed(feed)
                log(f"[rss:{feed.name}] collected {len(items)} items")
                all_items.extend(items)
            except Exception as exc:  # noqa: BLE001 - isolate per-feed failure
                log(f"[rss:{feed.name}] failed: {exc}")
        return sort_and_trim(all_items)

    def _collect_feed(self, feed: config.RSSFeed) -> List[NewsItem]:
        resp = self._request(feed.url)
        if resp is None:
            return []

        parsed = feedparser.parse(resp.content)
        items: List[NewsItem] = []
        for entry in parsed.entries:
            title = _strip_html(entry.get("title", "")).strip()
            url = entry.get("link", "").strip()
            if not title or not url:
                continue

            raw_summary = (
                entry.get("summary")
                or entry.get("description")
                or ""
            )
            summary = self._first_sentences(_strip_html(raw_summary))

            # Relevance filter (many of these feeds are already AI-scoped, but
            # this drops the occasional off-topic post).
            if not is_ai_related(title, summary):
                continue

            category = guess_category(title, summary, feed.default_category)
            published = self._iso_utc(_entry_datetime(entry))

            # Strict SGT time-window filter. Dated entries outside the window
            # are dropped; entries whose date we couldn't parse are kept only
            # when the feed itself supplied no timestamp at all (rare) so we
            # never silently lose a feed that stopped emitting dates.
            if not in_time_window(published, keep_undated=False):
                continue

            items.append(
                self.make_item(
                    title=title,
                    url=url,
                    source=feed.name,
                    summary=summary,
                    category=category,
                    published_at=published,
                    image_url=_entry_image_url(entry, feed.url),
                )
            )

        return sort_and_trim(items, config.MAX_ITEMS_PER_SOURCE)
