"""Base classes and shared helpers for all collectors.

Every collector returns a list of dicts conforming to the unified schema:

    {
        "title": str,
        "url": str,
        "source": str,               # techcrunch | hackernews | arxiv | ...
        "summary": str,              # 1-2 sentence summary
        "category": str,             # product | funding | paper | policy | other
        "published_at": str,         # ISO-8601 UTC, e.g. 2026-08-07T10:00:00Z
    }
"""
from __future__ import annotations

import sys
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

from .. import config

NewsItem = Dict[str, Any]


def log(message: str) -> None:
    """Emit a diagnostic message to stderr.

    All progress/statistics output goes to stderr so that stdout stays a clean,
    pipeable JSON payload.
    """
    print(message, file=sys.stderr)


class BaseCollector(ABC):
    """Abstract collector with shared HTTP + normalization utilities."""

    #: short, stable source identifier used in the `source` field
    source_name: str = "base"

    def __init__(self, session: Optional[requests.Session] = None) -> None:
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": config.USER_AGENT})

    # -- Public API ---------------------------------------------------------
    @abstractmethod
    def collect(self) -> List[NewsItem]:
        """Collect and return normalized news items for this source."""
        raise NotImplementedError

    # -- HTTP helpers -------------------------------------------------------
    def _request(
        self,
        url: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        timeout: Optional[int] = None,
        retries: Optional[int] = None,
    ) -> Optional[requests.Response]:
        """GET with timeout + retry. Returns None on total failure.

        A single request never raises to the caller so one bad source cannot
        abort the whole pipeline (fault isolation happens at the runner level
        too, but this keeps intra-collector loops resilient).
        """
        timeout = timeout if timeout is not None else config.HTTP_TIMEOUT
        retries = retries if retries is not None else config.HTTP_RETRIES

        last_err: Optional[Exception] = None
        for attempt in range(1, retries + 1):
            try:
                resp = self.session.get(url, params=params, timeout=timeout)
                resp.raise_for_status()
                return resp
            except Exception as exc:  # noqa: BLE001 - broad by design
                last_err = exc
                if attempt < retries:
                    # simple exponential-ish backoff, capped
                    time.sleep(min(2 ** (attempt - 1), 4))
        if last_err is not None:
            log(
                f"[{self.source_name}] request failed after {retries} "
                f"attempts: {url} ({last_err})"
            )
        return None

    # -- Normalization helpers ---------------------------------------------
    @staticmethod
    def _iso_utc(dt: Optional[datetime]) -> str:
        """Format a datetime as ISO-8601 UTC with a trailing Z.

        Falls back to 'now' if no datetime is supplied so items always sort.
        """
        if dt is None:
            dt = datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def _first_sentences(text: str, max_sentences: int = 2) -> str:
        """Return roughly the first 1-2 sentences of a blob of text."""
        if not text:
            return ""
        clean = " ".join(text.split())
        # Split on sentence terminators (western + CJK).
        out: List[str] = []
        buf = ""
        for ch in clean:
            buf += ch
            if ch in ".!?。！？":
                out.append(buf.strip())
                buf = ""
                if len(out) >= max_sentences:
                    break
        if buf.strip() and len(out) < max_sentences:
            out.append(buf.strip())
        summary = " ".join(out).strip()
        # Guard against pathological single "sentences".
        if len(summary) > 400:
            summary = summary[:397].rstrip() + "..."
        return summary

    @staticmethod
    def make_item(
        *,
        title: str,
        url: str,
        source: str,
        summary: str = "",
        category: str = "other",
        published_at: Optional[str] = None,
    ) -> NewsItem:
        """Build a schema-conforming item dict."""
        if category not in config.VALID_CATEGORIES:
            category = "other"
        return {
            "title": (title or "").strip(),
            "url": (url or "").strip(),
            "source": source,
            "summary": (summary or "").strip(),
            "category": category,
            "published_at": published_at
            or BaseCollector._iso_utc(None),
        }


def is_ai_related(*texts: str) -> bool:
    """Return True if any keyword appears in the concatenated text."""
    blob = " ".join(t for t in texts if t).lower()
    if not blob:
        return False
    return any(kw in blob for kw in config.AI_KEYWORDS)


def guess_category(title: str, summary: str, default: str = "other") -> str:
    """Very lightweight keyword-based category heuristic."""
    blob = f"{title} {summary}".lower()
    funding_kw = [
        "raise", "raises", "raised", "funding", "series a", "series b",
        "series c", "seed round", "valuation", "investment", "invests",
        "acquire", "acquisition", "ipo", "融资", "投资", "估值", "收购",
    ]
    policy_kw = [
        "regulat", "policy", "law", "ban", "lawsuit", "court", "senate",
        "congress", "eu ai act", "gdpr", "监管", "政策", "法案", "立法",
    ]
    paper_kw = ["arxiv", "paper", "we propose", "benchmark", "论文"]

    if any(k in blob for k in funding_kw):
        return "funding"
    if any(k in blob for k in policy_kw):
        return "policy"
    if any(k in blob for k in paper_kw):
        return "paper"
    return default


def sort_and_trim(items: List[NewsItem], max_items: int = 0) -> List[NewsItem]:
    """Sort items by published_at desc and optionally cap the count."""
    items = sorted(
        items, key=lambda it: it.get("published_at", ""), reverse=True
    )
    if max_items and max_items > 0:
        items = items[:max_items]
    return items
