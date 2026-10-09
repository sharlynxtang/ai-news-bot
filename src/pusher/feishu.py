"""Feishu (Lark) webhook pusher.

Converts a Markdown daily digest (produced by ``src.summarizer``) into a
Feishu **interactive message card** and POSTs it to a custom-bot webhook.

Webhook URL format:
    https://open.feishu.cn/open-apis/bot/v2/hook/{token}

The URL is read from the ``FEISHU_WEBHOOK_URL`` environment variable (via
``src.config``) unless one is passed explicitly.

The Markdown produced by the summarizer looks like::

    📊 每日 AI 资讯早报 | 2026-08-07 周五

    ━━ AI 产品与应用 ━━
    • 标题：摘要 [来源](https://…)
    • …

    ━━ 融资与收购 ━━
    • …

    ━━ 今日焦点 ━━
    分析文本…

The first non-empty line becomes the card ``header`` title; each
``━━ … ━━`` section becomes its own ``markdown`` element, separated by ``hr``
dividers.
"""
from __future__ import annotations

import json
import re
import sys
from typing import Any, Dict, List, Optional

import requests

from .. import config
from .images import FeishuImageUploader

# Feishu card header colour template (blue per spec).
HEADER_TEMPLATE = "blue"

# Matches a section header line like "━━ AI 产品与应用 ━━".
_SECTION_RE = re.compile(r"^━━\s*(.+?)\s*━━$")
_IMAGE_RE = re.compile(r"^🖼 \[原文配图\]\((https://[^)]+)\)$", re.MULTILINE)
MAX_INLINE_IMAGES = 3


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


def _split_sections(markdown: str) -> tuple[str, List[Dict[str, str]]]:
    """Split a digest Markdown string into (title, sections).

    Returns the header title (first non-empty line) and a list of sections,
    each ``{"label": <section label>, "body": <joined body text>}``. Content
    that appears before the first ``━━ … ━━`` marker (other than the title)
    is attached to a leading untitled section so nothing is dropped.
    """
    lines = markdown.splitlines()

    # ── title = first non-empty line ─────────────────────────────────────
    title = ""
    start = 0
    for idx, line in enumerate(lines):
        if line.strip():
            title = line.strip()
            start = idx + 1
            break

    sections: List[Dict[str, str]] = []
    current_label: Optional[str] = None
    current_body: List[str] = []

    def _flush() -> None:
        # Keep a section if it has a label or any non-empty body content.
        body = "\n".join(current_body).strip()
        if current_label is not None or body:
            sections.append({"label": current_label or "", "body": body})

    for line in lines[start:]:
        m = _SECTION_RE.match(line.strip())
        if m:
            _flush()
            current_label = m.group(1).strip()
            current_body = []
        else:
            current_body.append(line)
    _flush()

    return title, sections


def _section_to_markdown(section: Dict[str, str]) -> str:
    """Render one section as Feishu-card markdown content.

    The label is bolded to mimic the ``━━ label ━━`` divider styling; the
    body is passed through untouched (Feishu markdown supports ``**bold**``,
    ``[text](url)`` links and ``\n`` line breaks).
    """
    label = section.get("label", "").strip()
    body = section.get("body", "").strip()
    if label:
        header = f"**━━ {label} ━━**"
        return f"{header}\n{body}" if body else header
    return body


def build_card(markdown: str, *, image_keys: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Convert a digest Markdown string into a Feishu interactive-card payload.

    Each section becomes a ``markdown`` element; sections are separated by
    ``hr`` dividers. The returned dict is the full request body
    (``{"msg_type": "interactive", "card": {...}}``).
    """
    title, sections = _split_sections(markdown)

    elements: List[Dict[str, Any]] = []
    image_keys = image_keys or {}
    for section in sections:
        content = _section_to_markdown(section)
        if not content.strip():
            continue
        if elements:
            elements.append({"tag": "hr"})
        lines: List[str] = []
        for line in content.splitlines():
            match = _IMAGE_RE.fullmatch(line.strip())
            key = image_keys.get(match.group(1)) if match else None
            if key:
                if lines:
                    elements.append({"tag": "markdown", "content": "\n".join(lines)})
                    lines = []
                elements.append({
                    "tag": "img", "img_key": key,
                    "alt": {"tag": "plain_text", "content": "原文配图"},
                    "mode": "fit_horizontal",
                })
                elements.append({"tag": "markdown", "content": line})
            else:
                lines.append(line)
        if lines:
            elements.append({"tag": "markdown", "content": "\n".join(lines)})

    # Guarantee at least one element so the card is always valid.
    if not elements:
        elements.append({"tag": "markdown", "content": markdown.strip() or "（空）"})

    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": title or "每日 AI 资讯早报",
                },
                "template": HEADER_TEMPLATE,
            },
            "elements": elements,
        },
    }


class FeishuPusher:
    """Posts a Markdown digest to a Feishu custom-bot webhook as a card.

    Parameters
    ----------
    webhook_url:
        Full webhook URL. Defaults to ``FEISHU_WEBHOOK_URL`` from ``config``.
    timeout:
        Per-request timeout in seconds (default 10).
    dry_run:
        When True, ``push`` builds the payload and prints it as JSON instead
        of sending it over the network. Always returns True.
    """

    def __init__(
        self,
        webhook_url: Optional[str] = None,
        *,
        timeout: int = 10,
        dry_run: bool = False,
    ) -> None:
        self.webhook_url = webhook_url or config.FEISHU_WEBHOOK_URL
        self.timeout = timeout
        self.dry_run = dry_run

    # ── internals ─────────────────────────────────────────────────────────

    def _url_ok(self) -> bool:
        """True when a real (non-placeholder) webhook URL is configured."""
        url = (self.webhook_url or "").strip()
        return bool(url) and "your-token" not in url

    def _send(self, payload: Dict[str, Any]) -> bool:
        """POST *payload*; return True on Feishu success (``code == 0``)."""
        if not self._url_ok():
            _log(
                "[pusher] no valid FEISHU_WEBHOOK_URL configured "
                "(set it in .env or the environment)"
            )
            return False

        try:
            resp = requests.post(
                self.webhook_url,
                json=payload,
                timeout=self.timeout,
                headers={"Content-Type": "application/json"},
            )
        except requests.RequestException as exc:
            _log(f"[pusher] request failed: {exc}")
            return False

        if resp.status_code != 200:
            _log(
                f"[pusher] HTTP {resp.status_code}: "
                f"{resp.text[:300]}"
            )
            return False

        # Feishu returns 200 even on logical errors; the body carries a
        # non-zero ``code`` and a ``msg`` describing the problem.
        try:
            data = resp.json()
        except ValueError:
            _log(f"[pusher] non-JSON response: {resp.text[:300]}")
            return False

        code = data.get("code", data.get("StatusCode", 0))
        if code in (0, None):
            _log("[pusher] message delivered")
            return True

        _log(f"[pusher] Feishu error code={code} msg={data.get('msg')}")
        return False

    # ── public API ────────────────────────────────────────────────────────

    def push(self, markdown: str) -> bool:
        """Send *markdown* as a Feishu card. Return True on success.

        In dry-run mode the payload is printed as pretty JSON and True is
        returned without any network call.
        """
        image_keys = {}
        if not self.dry_run and config.FEISHU_APP_ID and config.FEISHU_APP_SECRET:
            urls = list(dict.fromkeys(_IMAGE_RE.findall(markdown)))[:MAX_INLINE_IMAGES]
            try:
                image_keys = FeishuImageUploader().upload_many(urls)
                if urls:
                    _log(f"[pusher] embedded {len(image_keys)}/{len(urls)} source images")
            except (requests.RequestException, ValueError):
                _log("[pusher] image upload unavailable; keeping source-image links")
        payload = build_card(markdown, image_keys=image_keys)

        if self.dry_run:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            _log("[pusher] dry-run: payload above was NOT sent")
            return True

        return self._send(payload)

    def test_connection(self) -> bool:
        """Send a minimal connectivity-test card. Return True on success.

        Useful to verify the webhook is reachable and valid before wiring up
        the full pipeline (exposed via ``python main.py --test-webhook``).
        """
        test_markdown = (
            "🔧 AI News Bot 连通性测试\n\n"
            "━━ 测试 ━━\n"
            "• 如果你能看到这条消息，说明飞书 Webhook 配置正确 ✅"
        )
        payload = build_card(test_markdown)

        if self.dry_run:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            _log("[pusher] dry-run: test payload above was NOT sent")
            return True

        _log("[pusher] sending connectivity test message…")
        return self._send(payload)


__all__ = ["FeishuPusher", "build_card"]
