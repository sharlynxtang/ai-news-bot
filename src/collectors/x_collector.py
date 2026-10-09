"""Curated AI posts from X's official recent-search API."""
from __future__ import annotations

from datetime import timezone
from typing import List
from urllib.parse import urlparse

from .. import config
from .base import BaseCollector, NewsItem, in_time_window, is_ai_related, log


class XCollector(BaseCollector):
    source_name = "x"

    def __init__(self, session=None, bearer_token: str | None = None) -> None:
        super().__init__(session=session)
        self.bearer_token = bearer_token if bearer_token is not None else config.X_BEARER_TOKEN

    def collect(self) -> List[NewsItem]:
        accounts = [a for a in config.X_ACCOUNTS if a.replace("_", "").isalnum()]
        if not self.bearer_token or not accounts:
            log("[x] skipped (X_BEARER_TOKEN or X_ACCOUNTS not configured)")
            return []

        start, end = config.get_time_window()
        self.session.headers["Authorization"] = f"Bearer {self.bearer_token}"
        params = {
            "query": "(" + " OR ".join(f"from:{a}" for a in accounts) + ") -is:retweet -is:reply",
            "start_time": start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "end_time": end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "max_results": 100,
            "tweet.fields": "author_id,created_at,public_metrics,attachments",
            "expansions": "author_id,attachments.media_keys",
            "user.fields": "username,name",
            "media.fields": "type,url,preview_image_url",
        }
        resp = self._request(config.X_API_URL, params=params, retries=1)
        if resp is None:
            return []
        try:
            payload = resp.json()
        except ValueError:
            log("[x] invalid JSON response")
            return []

        included = payload.get("includes") or {}
        users = {str(u.get("id")): u.get("username", "") for u in (included.get("users") or [])}
        media = {m.get("media_key"): m for m in (included.get("media") or [])}
        allowed = {a.lower() for a in accounts}
        ranked = []
        for post in (payload.get("data") or []):
            username = users.get(str(post.get("author_id")), "")
            body = " ".join((post.get("text") or "").split())
            published = post.get("created_at", "")
            if username.lower() not in allowed or not body or not is_ai_related(body):
                continue
            if not in_time_window(published):
                continue
            post_id = str(post.get("id", ""))
            if not post_id.isdigit():
                continue
            image_url = ""
            for key in (post.get("attachments") or {}).get("media_keys", []):
                item = media.get(key, {})
                candidate = item.get("url") if item.get("type") == "photo" else item.get("preview_image_url")
                parsed = urlparse(candidate or "")
                if parsed.scheme == "https" and parsed.hostname:
                    image_url = candidate
                    break
            metrics = post.get("public_metrics") or {}
            score = int(metrics.get("like_count") or 0) + 2 * int(metrics.get("retweet_count") or 0)
            item = self.make_item(
                title=f"@{username} 的观点",
                url=f"https://x.com/{username}/status/{post_id}",
                source="x",
                summary=body,
                category="x_post",
                published_at=published,
                image_url=image_url,
            )
            ranked.append((score, published, item))
        ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
        items = [row[2] for row in ranked[: max(config.X_MAX_POSTS, 0)]]
        log(f"[x] collected {len(items)} posts")
        return items
