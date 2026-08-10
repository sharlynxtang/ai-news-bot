"""Centralized configuration for the AI News Bot.

Values are loaded from environment variables (optionally via a local `.env`
file). Every setting has a sensible default so the collectors can run without
any configuration for a quick smoke test.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List

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

# --- Secrets (consumed by summarizer / pusher, stubbed for now) ------------
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
FEISHU_WEBHOOK_URL: str = os.getenv("FEISHU_WEBHOOK_URL", "")


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
    # Chinese source: 机器之心 (Synced). If the feed is unavailable at runtime
    # the RSS collector isolates the failure and simply reports 0 items.
    RSSFeed(
        name="jiqizhixin",
        url="https://www.jiqizhixin.com/rss",
        default_category="other",
    ),
]

# --- arXiv -----------------------------------------------------------------
ARXIV_API_URL: str = "http://export.arxiv.org/api/query"
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
    "算法", "生成式", "智能体",
]

VALID_CATEGORIES = {"product", "funding", "paper", "policy", "other"}
