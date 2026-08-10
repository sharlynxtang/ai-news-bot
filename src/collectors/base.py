"""Base classes and shared helpers for all collectors.

Every collector returns a list of dicts conforming to the unified schema:

    {
        "title": str,
        "url": str,
        "source": str,               # techcrunch | hackernews | arxiv | ...
        "summary": str,              # 1-2 sentence summary
        "category": str,             # product | paper | policy | competitor_coding | competitor_agent | other
        "published_at": str,         # ISO-8601 UTC, e.g. 2026-08-07T10:00:00Z
    }
"""
from __future__ import annotations

import sys
import time
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
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


def parse_iso_utc(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 UTC timestamp (with trailing Z) into a datetime.

    Returns None when *value* is empty or unparseable so callers can decide how
    to treat items lacking a reliable timestamp.
    """
    if not value:
        return None
    txt = value.strip()
    if txt.endswith("Z"):
        txt = txt[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(txt)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def in_time_window(
    published_at: Optional[str],
    *,
    slack_hours: int = 0,
    keep_undated: bool = False,
) -> bool:
    """Return True if *published_at* falls inside the strict SGT window.

    The window is ``config.get_time_window()`` ([start, end] in UTC). A
    ``slack_hours`` value widens the *start* side only (used by arXiv, whose
    publication lag can push a relevant paper slightly before the window).

    ``keep_undated`` controls the policy for items with no parseable timestamp:
    sources that expose no per-item dates (e.g. the atyun scraper) pass
    ``True`` so they are not silently dropped, while dated feeds pass ``False``.
    """
    dt = parse_iso_utc(published_at)
    if dt is None:
        return keep_undated
    start, end = config.get_time_window()
    if slack_hours:
        start = start - timedelta(hours=slack_hours)
    return start <= dt <= end


def date_label_cn(published_at: Optional[str]) -> str:
    """Format an item's date as a Chinese ``（8月9日）`` label (local tz).

    Returns an empty string when the timestamp is missing/unparseable so
    callers can omit the label rather than print a wrong date.
    """
    dt = parse_iso_utc(published_at)
    if dt is None:
        return ""
    local = dt.astimezone(config._local_tz())
    return f"（{local.month}月{local.day}日）"


def guess_category(title: str, summary: str, default: str = "other") -> str:
    """Very lightweight keyword-based category heuristic.

    Categories: product / paper / policy / competitor_coding /
    competitor_agent / other. There is intentionally no `funding` category —
    funding/acquisition coverage is deferred to the sibling "AI Briefing" bot;
    a competitor's fundraise still lands in its competitor section, everything
    else falls through to `product`/`other`.

    `policy` is narrowed to **data & AI compliance / regulation** only: GDPR,
    data-protection laws, AI governance/act, algorithm filing, AI ethics and
    AI-training copyright. Generic security breaches / hacks are NOT policy.
    """
    blob = f"{title} {summary}".lower()

    # Data & AI compliance / regulation (narrowed — no generic cybersecurity).
    policy_kw = [
        # data compliance
        "compliance", "gdpr", "data protection", "data privacy",
        "privacy law", "data transfer", "cross-border data", "data security",
        "数据合规", "个人信息保护", "数据安全", "数据跨境", "隐私保护",
        # AI governance / regulation
        "ai governance", "ai regulat", "ai act", "eu ai act", "ai law",
        "ai policy", "algorithm filing", "responsible ai", "ai ethics",
        "ai safety standard", "ai oversight",
        "ai治理", "算法备案", "ai监管", "ai法案", "ai伦理",
        "ai安全标准", "人工智能管理办法", "生成式人工智能",
        # copyright / IP tied to AI
        "copyright ai", "ai copyright", "training data copyright",
        "ai训练数据", "知识产权", "版权",
    ]
    paper_kw = ["arxiv", "paper", "we propose", "benchmark", "论文"]

    # Compliance/regulation is checked first so an AI-compliance headline that
    # also mentions a product name lands in the regulation section.
    if any(k in blob for k in policy_kw):
        return "policy"
    if any(k in blob for k in paper_kw):
        return "paper"
    # Competitor product tracking, split into two disjoint sub-categories.
    # Coding tools take precedence over agent platforms when both would match.
    if any(k in blob for k in config.CODING_COMPETITOR_KEYWORDS):
        return "competitor_coding"
    if any(k in blob for k in config.AGENT_COMPETITOR_KEYWORDS):
        return "competitor_agent"
    return default


def sort_and_trim(items: List[NewsItem], max_items: int = 0) -> List[NewsItem]:
    """Sort items by published_at desc and optionally cap the count."""
    items = sorted(
        items, key=lambda it: it.get("published_at", ""), reverse=True
    )
    if max_items and max_items > 0:
        items = items[:max_items]
    return items
