"""Centralized configuration for the AI News Bot.

Values are loaded from environment variables (optionally via a local `.env`
file). Every setting has a sensible default so the collectors can run without
any configuration for a quick smoke test.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, time as dt_time, timedelta, timezone
from typing import List, Optional, Tuple

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - zoneinfo is stdlib on py3.9+
    ZoneInfo = None  # type: ignore

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv is optional at runtime
    pass


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# --- HTTP behaviour ---------------------------------------------------------
HTTP_TIMEOUT: int = _get_int("HTTP_TIMEOUT", 10)
HTTP_RETRIES: int = _get_int("HTTP_RETRIES", 3)
USER_AGENT: str = (
    "ai-news-bot/0.1 (+https://github.com/your-org/ai-news-bot)"
)

# --- Collection windows / thresholds ---------------------------------------
LOOKBACK_HOURS: int = _get_int("LOOKBACK_HOURS", 24)
HN_MIN_POINTS: int = _get_int("HN_MIN_POINTS", 50)
MAX_ITEMS_PER_SOURCE: int = _get_int("MAX_ITEMS_PER_SOURCE", 30)

# --- Strict time window (Singapore time, UTC+8) ----------------------------
# We only keep news from the previous day 10:30 → today 10:30 (SGT). On Monday
# the window widens back to the previous Friday 10:30 so the weekend is covered.
TIMEZONE: str = os.getenv("TIMEZONE", "Asia/Singapore")
# Daily cutoff hour/minute (local time) that bounds the window.
WINDOW_CUTOFF_HOUR: int = _get_int("WINDOW_CUTOFF_HOUR", 10)
WINDOW_CUTOFF_MINUTE: int = _get_int("WINDOW_CUTOFF_MINUTE", 30)
# arXiv publishes with a delay, so its window is relaxed by this many hours on
# the *start* side only (earlier papers may still surface within our window).
ARXIV_WINDOW_SLACK_HOURS: int = _get_int("ARXIV_WINDOW_SLACK_HOURS", 12)

# Fixed +08:00 fallback used when the tz database is unavailable.
_SGT_FALLBACK = timezone(timedelta(hours=8))


def _local_tz() -> timezone:
    """Return the configured local timezone, falling back to fixed +08:00."""
    if ZoneInfo is not None:
        try:
            return ZoneInfo(TIMEZONE)  # type: ignore[return-value]
        except Exception:  # pragma: no cover - bad tz name
            pass
    return _SGT_FALLBACK


def get_time_window(now: Optional[datetime] = None) -> Tuple[datetime, datetime]:
    """Compute the [start, end] collection window in UTC.

    The window end is *today* at ``WINDOW_CUTOFF_HOUR:MINUTE`` local time (or,
    if the current local time is still before that cutoff, *yesterday*'s cutoff
    — so a run at 09:00 reports the window that just closed the day before).
    The window start is the previous local cutoff, except on Monday where it
    reaches back to the previous Friday's cutoff (covering the weekend).

    Returns ``(start_utc, end_utc)`` as timezone-aware UTC datetimes.
    """
    tz = _local_tz()
    now_local = (now or datetime.now(timezone.utc)).astimezone(tz)
    cutoff = dt_time(WINDOW_CUTOFF_HOUR, WINDOW_CUTOFF_MINUTE)

    # End = most recent cutoff at-or-before now.
    end_local = now_local.replace(
        hour=WINDOW_CUTOFF_HOUR,
        minute=WINDOW_CUTOFF_MINUTE,
        second=0,
        microsecond=0,
    )
    if now_local.time() < cutoff:
        end_local -= timedelta(days=1)

    # Start = previous cutoff; Monday reaches back over the weekend to Friday.
    # weekday(): Mon=0 … Sun=6. If the window END lands on a Monday we widen it.
    if end_local.weekday() == 0:  # Monday
        start_local = end_local - timedelta(days=3)  # back to Friday
    else:
        start_local = end_local - timedelta(days=1)

    return (
        start_local.astimezone(timezone.utc),
        end_local.astimezone(timezone.utc),
    )


def window_lookback_hours(now: Optional[datetime] = None) -> int:
    """Width of the current time window in whole hours (24 or 72)."""
    start, end = get_time_window(now)
    return int(round((end - start).total_seconds() / 3600))

# --- Secrets (consumed by summarizer / pusher) ------------------------------
# Supports any OpenAI-compatible API endpoint.
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
FEISHU_WEBHOOK_URL: str = os.getenv("FEISHU_WEBHOOK_URL", "")
FEISHU_APP_ID: str = os.getenv("FEISHU_APP_ID", "")
FEISHU_APP_SECRET: str = os.getenv("FEISHU_APP_SECRET", "")

# X's official API is optional. Without a bearer token the existing news
# sources continue to work; with one, recent posts from this curated list are
# considered for the daily digest. Override the list with X_ACCOUNTS.
X_BEARER_TOKEN: str = os.getenv("X_BEARER_TOKEN", "")
X_API_URL: str = "https://api.x.com/2/tweets/search/recent"
X_ACCOUNTS: List[str] = [
    account.strip().lstrip("@")
    for account in os.getenv(
        "X_ACCOUNTS",
        "karpathy,AndrewYNg,ylecun,demishassabis,fchollet,JimFan,OpenAI,AnthropicAI,GoogleDeepMind",
    ).split(",")
    if account.strip()
]
X_MAX_POSTS: int = _get_int("X_MAX_POSTS", 4)


@dataclass(frozen=True)
class RSSFeed:
    """A single RSS source definition."""

    name: str          # short source id, e.g. "techcrunch"
    url: str
    default_category: str = "other"


# --- RSS sources ------------------------------------------------------------
# Note: arXiv also exposes RSS but is handled by a dedicated collector so we
# can parse categories and abstracts properly.
RSS_FEEDS: List[RSSFeed] = [
    RSSFeed(
        name="techcrunch",
        url="https://techcrunch.com/category/artificial-intelligence/feed/",
        default_category="product",
    ),
    RSSFeed(
        name="theverge",
        url="https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
        default_category="product",
    ),
    RSSFeed(
        name="arstechnica",
        url="https://feeds.arstechnica.com/arstechnica/technology-lab",
        default_category="product",
    ),
    # --- Chinese sources ------------------------------------------------------
    # 量子位 — one of China's top AI media, covers research + industry
    RSSFeed(
        name="qbitai",
        url="https://www.qbitai.com/feed",
        default_category="product",
    ),
    # 雷锋网 — AI/robotics/industry news
    RSSFeed(
        name="leiphone",
        url="https://www.leiphone.com/feed",
        default_category="product",
    ),
    # InfoQ AI 中文频道 — engineering-focused AI articles
    RSSFeed(
        name="infoq_ai",
        url="https://www.infoq.cn/feed?type=ai",
        default_category="product",
    ),
    # 机器之心 (Synced). If the feed is unavailable at runtime
    # the RSS collector isolates the failure and simply reports 0 items.
    RSSFeed(
        name="jiqizhixin",
        url="https://www.jiqizhixin.com/rss",
        default_category="other",
    ),
]

# --- Competitor RSS/blog sources -------------------------------------------
# Official blogs / update feeds for tracked AI Coding & AI Agent products.
# These are appended to RSS_FEEDS below so they flow through the same generic
# RSS collector. Feeds that are unavailable at runtime simply report 0 items
# (fault isolation happens per-feed in the RSS collector). Where a vendor does
# not publish a dedicated RSS/Atom feed we omit it rather than guess a URL.
COMPETITOR_RSS_FEEDS: List[RSSFeed] = [
    # AI Coding vendors
    RSSFeed(
        name="github_blog_ai",
        url="https://github.blog/ai-and-ml/feed/",
        default_category="competitor_coding",
    ),
    RSSFeed(
        name="replit_blog",
        url="https://blog.replit.com/feed.xml",
        default_category="competitor_coding",
    ),
    RSSFeed(
        name="cursor_blog",
        url="https://www.cursor.com/blog/rss.xml",
        default_category="competitor_coding",
    ),
    RSSFeed(
        name="codeium_blog",
        url="https://codeium.com/blog/rss.xml",
        default_category="competitor_coding",
    ),
    # AI Agent / app-building platforms
    RSSFeed(
        name="vercel_blog",
        url="https://vercel.com/atom",
        default_category="competitor_agent",
    ),
    RSSFeed(
        name="langchain_blog",
        url="https://blog.langchain.dev/rss/",
        default_category="competitor_agent",
    ),
]

# Competitor feeds share the same generic RSS collector pipeline.
RSS_FEEDS = RSS_FEEDS + COMPETITOR_RSS_FEEDS

# --- arXiv -----------------------------------------------------------------
ARXIV_API_URL: str = "https://export.arxiv.org/api/query"
ARXIV_CATEGORIES: List[str] = ["cs.AI", "cs.CL", "cs.CV", "cs.LG"]

# --- Hacker News (Algolia) --------------------------------------------------
HN_API_URL: str = "https://hn.algolia.com/api/v1/search"
HN_QUERIES: List[str] = [
    "AI",
    "LLM",
    "GPT",
    "Claude",
    "machine learning",
    "deep learning",
    # Competitor product tracking (AI Coding / AI Agent)
    "Cursor editor",
    "Windsurf",
    "Codeium",
    "Lovable",
    "Manus AI",
    "v0 Vercel",
    "Bolt.new",
    "Devin AI",
    "Harvey AI",
    "Augment Code",
    "Claude Code",
    "Qoder",
    "OpenAI Codex",
]

# --- Relevance filtering ----------------------------------------------------
# Items whose title/summary contain none of these keywords are dropped by
# collectors that opt into keyword filtering (RSS, HN). arXiv/Chinese feeds
# that are already AI-scoped skip this filter.
AI_KEYWORDS: List[str] = [
    "ai", "a.i.", "artificial intelligence", "machine learning", "ml",
    "deep learning", "neural", "llm", "large language model", "gpt",
    "chatgpt", "claude", "gemini", "openai", "anthropic", "mistral",
    "llama", "transformer", "generative", "diffusion", "stable diffusion",
    "midjourney", "agent", "rag", "fine-tun", "inference", "embedding",
    "computer vision", "nlp", "reinforcement learning", "model",
    # Chinese keywords
    "人工智能", "机器学习", "深度学习", "大模型", "神经网络", "模型",
    "算法", "生成式", "智能体", "强化学习", "预训练", "微调",
    "多模态", "语言模型", "视觉模型", "扩散模型", "提示词",
]

# --- Competitor tracking ----------------------------------------------------
# Product names used to (a) route matching news into the competitor
# sub-categories (see collectors.base.guess_category) and (b) keep competitor
# stories from being dropped by the AI relevance filter (they are merged into
# AI_KEYWORDS below).
#
# Two disjoint buckets so we can render two distinct sections:
#   - CODING_COMPETITOR_KEYWORDS → category "competitor_coding"
#   - AGENT_COMPETITOR_KEYWORDS  → category "competitor_agent"

# AI 编程工具 (IDE / CLI / code-completion / autonomous coding agents)
CODING_COMPETITOR_KEYWORDS: List[str] = [
    "cursor", "windsurf", "codeium", "github copilot", "copilot",
    "augment code", "cline", "replit agent", "replit", "devin",
    "amazon q developer", "jetbrains ai", "tabnine", "sourcegraph", "cody",
    "codex cli", "codex", "claude code", "qoder",
]

# AI Agent / 应用构建平台 + AI+垂直行业应用
AGENT_COMPETITOR_KEYWORDS: List[str] = [
    # Agent / app-building platforms
    "manus", "lovable", "bolt.new", "v0.dev", "vercel v0", "v0",
    "autogpt", "crewai", "langchain", "dify", "coze",
    "flowise", "n8n ai", "n8n", "make ai",
    "workbuddy", "千问办公", "通义千问办公", "qwen office",
    # AI + vertical applications
    "harvey ai", "cocounsel", "casetext",
    "hirevue", "paradox ai", "eightfold",
    "kensho", "bloomberg gpt", "alphasense",
]

# Backward-compatible union: still used by the AI relevance filter so competitor
# coverage from general feeds (TechCrunch, HN, …) is not dropped before it can
# be categorized.
COMPETITOR_KEYWORDS: List[str] = (
    CODING_COMPETITOR_KEYWORDS + AGENT_COMPETITOR_KEYWORDS
)

# Competitor names are AI-relevant by definition: fold them into the relevance
# filter so competitor coverage from general feeds is not dropped early.
AI_KEYWORDS = AI_KEYWORDS + COMPETITOR_KEYWORDS

VALID_CATEGORIES = {
    "product", "paper", "policy",
    "competitor_coding", "competitor_agent", "x_post", "other",
}
