"""LLM-based news summarizer.

Accepts a ``list[NewsItem]`` from the collectors pipeline and returns a
formatted Markdown daily digest.

Modes:
  - Normal  : calls the OpenAI-compatible API configured in ``src.config``.
  - Dry-run : skips the API and uses a simple template (``dry_run=True`` or
              when ``OPENAI_API_KEY`` is absent / a placeholder value).
  - Fallback: same template as dry-run, activated automatically when an API
              call fails at runtime.

Batching: when the input exceeds ``BATCH_SIZE`` (50) items the pipeline runs
two passes — a "select" pass over fixed-size batches, then a "final" pass
over the merged candidates — so we never send a prohibitively large prompt.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

from .. import config

NewsItem = Dict[str, Any]

# ── constants ──────────────────────────────────────────────────────────────
BATCH_SIZE = 30          # items per first-pass LLM call
FIRST_PASS_TARGET = 10   # items to keep per batch in first pass
FINAL_MIN = 15           # minimum items in final digest
FINAL_MAX = 25           # maximum items in final digest
SUMMARY_MAX_LEN = 300    # chars; longer summaries are truncated before sending

# Category display labels (order is preserved in the final Markdown)
CATEGORY_LABELS: Dict[str, str] = {
    "product": "AI 产品与应用",
    "funding": "融资与收购",
    "paper":   "论文与技术突破",
    "policy":  "政策与监管",
}

# The "other" bucket is rendered after the four main sections only when
# it actually contains items.
OTHER_LABEL = "其他动态"

WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


# ── helpers ────────────────────────────────────────────────────────────────

def _preprocess(items: List[NewsItem]) -> List[NewsItem]:
    """Truncate long summaries to keep token counts reasonable."""
    result = []
    for it in items:
        copy = dict(it)
        s = copy.get("summary", "")
        if len(s) > SUMMARY_MAX_LEN:
            copy["summary"] = s[:SUMMARY_MAX_LEN - 3] + "..."
        result.append(copy)
    return result


def _strip_code_fence(text: str) -> str:
    """Remove markdown code fences that some models wrap JSON in."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        # drop first line (```json or ```) and last line (```)
        inner = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
        text = "\n".join(inner)
    return text.strip()


def _parse_json_response(text: str) -> Dict[str, Any]:
    return json.loads(_strip_code_fence(text))


def _apply_llm_selection(
    original: List[NewsItem],
    llm_items: List[Dict[str, Any]],
) -> List[NewsItem]:
    """Merge LLM-generated fields (summary_cn, category) back into original items."""
    id_map = {i: it for i, it in enumerate(original)}
    result: List[NewsItem] = []
    for llm_it in llm_items:
        orig_id = llm_it.get("id")
        if orig_id is None or orig_id not in id_map:
            continue
        orig = dict(id_map[orig_id])
        orig["summary_cn"] = llm_it.get("summary_cn", orig.get("summary", ""))
        cat = llm_it.get("category", orig.get("category", "other"))
        orig["category"] = cat if cat in {*CATEGORY_LABELS, "other"} else "other"
        result.append(orig)
    return result


# ── prompt builders ────────────────────────────────────────────────────────

def _build_batch_prompt(items: List[NewsItem], target: int) -> List[Dict[str, str]]:
    """First-pass prompt: select top items from a single batch."""
    payload = [
        {
            "id": i,
            "title": it.get("title", ""),
            "source": it.get("source", ""),
            "category": it.get("category", "other"),
            "summary": it.get("summary", ""),
        }
        for i, it in enumerate(items)
    ]
    items_json = json.dumps(payload, ensure_ascii=False)

    return [
        {
            "role": "system",
            "content": (
                "你是一位专业的 AI 行业资讯编辑，精通中英文。"
                "你的任务是从大量原始新闻中筛选出最重要的条目并为其生成简洁的中文摘要。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"以下是 {len(items)} 条 AI 新闻。请选出最重要的 {target} 条，"
                "并为每条生成 1-2 句精炼中文摘要。\n\n"
                "选择标准：\n"
                "1. 优先选择有实质性进展的新闻（重大产品发布、大额融资、突破性论文、重要政策）\n"
                "2. 去重：同一事件的多条报道只保留最重要的一条\n"
                "3. 校正 category（可选值：product / funding / paper / policy / other）\n\n"
                f"新闻列表：\n{items_json}\n\n"
                "请以纯 JSON 格式返回（不要包含 ```代码块``` 标记）：\n"
                '{"items":[{"id":<原始id>,"summary_cn":"<中文摘要>","category":"<category>"}]}'
            ),
        },
    ]


def _build_final_prompt(items: List[NewsItem]) -> List[Dict[str, str]]:
    """Final-pass prompt: select top 15-25, produce Chinese summaries + focus."""
    payload = [
        {
            "id": i,
            "title": it.get("title", ""),
            "url": it.get("url", ""),
            "source": it.get("source", ""),
            "category": it.get("category", "other"),
            # Prefer already-generated Chinese summary if available
            "summary": it.get("summary_cn", it.get("summary", "")),
        }
        for i, it in enumerate(items)
    ]
    items_json = json.dumps(payload, ensure_ascii=False)

    return [
        {
            "role": "system",
            "content": (
                "你是一位专业的 AI 行业资讯编辑，精通中英文。"
                "你的任务是从候选新闻中精选每日早报条目并撰写今日焦点分析。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"以下是 {len(items)} 条候选 AI 新闻。请完成以下任务：\n\n"
                f"1. 筛选：选出最重要的 {FINAL_MIN}–{FINAL_MAX} 条\n"
                "2. 去重：同一事件的不同来源报道合并为一条（保留最有价值的 URL）\n"
                "3. 分类校正：确认/修正 category（product / funding / paper / policy / other）\n"
                "4. 中文摘要：每条 1-2 句精炼中文摘要（英文原文需翻译）\n"
                "5. 今日焦点：2-3 句综合分析，指出当日最值得关注的趋势或事件\n\n"
                f"新闻列表：\n{items_json}\n\n"
                "请以纯 JSON 格式返回（不要包含 ```代码块``` 标记）：\n"
                '{"items":[{"title":"<标题>","url":"<url>","summary_cn":"<中文摘要>",'
                '"category":"<category>"}],"focus_analysis":"<今日焦点>"}'
            ),
        },
    ]


# ── main class ─────────────────────────────────────────────────────────────

class LLMSummarizer:
    """Produces a Markdown daily digest from a list of collected news items.

    Parameters
    ----------
    dry_run:
        When True, skip the LLM and use the template fallback directly.
    api_key:
        OpenAI-compatible API key. Defaults to ``OPENAI_API_KEY`` env var.
    base_url:
        API base URL. Defaults to ``OPENAI_BASE_URL`` env var.
    model:
        Model name. Defaults to ``OPENAI_MODEL`` env var.
    """

    def __init__(
        self,
        *,
        dry_run: bool = False,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ) -> None:
        self.dry_run = dry_run
        self.api_key = api_key or config.OPENAI_API_KEY
        self.base_url = (base_url or config.OPENAI_BASE_URL).rstrip("/")
        self.model = model or config.OPENAI_MODEL

    # ── public ───────────────────────────────────────────────────────────

    def summarize(self, items: List[NewsItem]) -> str:
        """Return a Markdown-formatted daily digest for *items*."""
        if not items:
            return self._empty_report()

        # Treat missing or placeholder key as "no key"
        key_ok = bool(self.api_key) and not self.api_key.startswith("sk-your")
        use_llm = not self.dry_run and key_ok

        if not use_llm:
            reason = "dry-run mode" if self.dry_run else "no valid API key"
            _log(f"[summarizer] using template fallback ({reason})")
            return self._fallback_format(items)

        try:
            return self._llm_pipeline(items)
        except Exception as exc:
            _log(f"[summarizer] LLM pipeline failed ({exc}), falling back to template")
            return self._fallback_format(items)

    # ── LLM pipeline ─────────────────────────────────────────────────────

    def _llm_pipeline(self, items: List[NewsItem]) -> str:
        items = _preprocess(items)

        if len(items) > BATCH_SIZE:
            _log(f"[summarizer] {len(items)} items > {BATCH_SIZE}, running multi-batch select")
            candidates = self._multi_batch_select(items)
        else:
            candidates = items

        _log(
            f"[summarizer] final pass: {len(candidates)} candidates "
            f"→ selecting {FINAL_MIN}–{FINAL_MAX}"
        )
        result = self._call_final(candidates)
        return self._render_markdown(result)

    def _multi_batch_select(self, items: List[NewsItem]) -> List[NewsItem]:
        """First pass: select top items independently from each batch."""
        batches = [
            items[i : i + BATCH_SIZE] for i in range(0, len(items), BATCH_SIZE)
        ]
        selected: List[NewsItem] = []
        for idx, batch in enumerate(batches, 1):
            _log(f"[summarizer] batch {idx}/{len(batches)} ({len(batch)} items)")
            prompt = _build_batch_prompt(batch, FIRST_PASS_TARGET)
            content = self._chat(prompt)
            if content is None:
                # API failed for this batch — keep raw top items as-is
                selected.extend(batch[:FIRST_PASS_TARGET])
                continue
            try:
                data = _parse_json_response(content)
                partial = _apply_llm_selection(batch, data.get("items", []))
                selected.extend(partial)
            except Exception as exc:
                _log(
                    f"[summarizer] batch {idx} parse error ({exc}), "
                    "keeping raw top items"
                )
                selected.extend(batch[:FIRST_PASS_TARGET])
        return selected

    def _call_final(self, items: List[NewsItem]) -> Dict[str, Any]:
        prompt = _build_final_prompt(items)
        content = self._chat(prompt)
        if content is None:
            raise RuntimeError("LLM returned no content for final pass")
        try:
            return _parse_json_response(content)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"LLM response was not valid JSON: {exc}") from exc

    def _chat(self, messages: List[Dict[str, str]]) -> Optional[str]:
        """POST to the OpenAI chat completions endpoint; returns content or None."""
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 4096,
        }
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=90)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except Exception as exc:
            _log(f"[summarizer] API call failed: {exc}")
            return None

    # ── fallback (no LLM) ────────────────────────────────────────────────

    def _fallback_format(self, items: List[NewsItem]) -> str:
        """Simple template-based digest. No LLM required."""
        # Keep up to 5 per category so all four required sections have content.
        by_cat: Dict[str, List[NewsItem]] = {k: [] for k in [*CATEGORY_LABELS, "other"]}
        for it in items:
            cat = it.get("category", "other")
            if cat not in by_cat:
                cat = "other"
            if len(by_cat[cat]) < 5:
                by_cat[cat].append(it)

        digest_items: List[Dict[str, Any]] = []
        for cat_items in by_cat.values():
            for it in cat_items:
                raw = it.get("summary", "") or it.get("title", "")
                # Keep summaries short in fallback — LLM will do better later.
                summary_cn = raw[:120] + ("..." if len(raw) > 120 else "")
                digest_items.append(
                    {
                        "title": it.get("title", ""),
                        "url": it.get("url", ""),
                        "summary_cn": summary_cn,
                        "category": it.get("category", "other"),
                    }
                )

        focus = (
            f"（模板模式 · 未调用 AI 分析）当日共采集到 {len(items)} 条 AI 资讯，"
            "涵盖产品发布、融资收购、论文突破与政策监管等多个领域，请查阅各分类获取详情。"
        )
        return self._render_markdown({"items": digest_items, "focus_analysis": focus})

    # ── Markdown renderer ─────────────────────────────────────────────────

    def _render_markdown(self, result: Dict[str, Any]) -> str:
        now_local = datetime.now(timezone.utc).astimezone()
        date_str = now_local.strftime("%Y-%m-%d")
        weekday_str = WEEKDAY_CN[now_local.weekday()]

        lines: List[str] = [f"📊 每日 AI 资讯早报 | {date_str} {weekday_str}", ""]

        # Index incoming items by category
        by_cat: Dict[str, List[Dict[str, Any]]] = {
            k: [] for k in [*CATEGORY_LABELS, "other"]
        }
        for it in result.get("items", []):
            cat = it.get("category", "other")
            if cat not in by_cat:
                cat = "other"
            by_cat[cat].append(it)

        # Always render all four required sections (acceptance criteria)
        for cat, label in CATEGORY_LABELS.items():
            lines.append(f"━━ {label} ━━")
            cat_items = by_cat[cat]
            if cat_items:
                for it in cat_items:
                    title   = (it.get("title") or "").strip()
                    summary = (it.get("summary_cn") or "").strip()
                    url     = (it.get("url") or "").strip()
                    entry   = f"• {title}：{summary}"
                    if url:
                        entry += f" [来源]({url})"
                    lines.append(entry)
            else:
                lines.append("• （暂无相关资讯）")
            lines.append("")

        # "Other" section only when it has content (not a required section)
        if by_cat["other"]:
            lines.append(f"━━ {OTHER_LABEL} ━━")
            for it in by_cat["other"]:
                title   = (it.get("title") or "").strip()
                summary = (it.get("summary_cn") or "").strip()
                url     = (it.get("url") or "").strip()
                entry   = f"• {title}：{summary}"
                if url:
                    entry += f" [来源]({url})"
                lines.append(entry)
            lines.append("")

        # Focus section (always present)
        focus = (result.get("focus_analysis") or "").strip()
        lines.append("━━ 今日焦点 ━━")
        lines.append(focus if focus else "（暂无）")
        lines.append("")

        return "\n".join(lines)

    def _empty_report(self) -> str:
        now_local = datetime.now(timezone.utc).astimezone()
        date_str = now_local.strftime("%Y-%m-%d")
        weekday_str = WEEKDAY_CN[now_local.weekday()]
        return (
            f"📊 每日 AI 资讯早报 | {date_str} {weekday_str}\n\n"
            "（今日未采集到任何 AI 资讯）\n"
        )
