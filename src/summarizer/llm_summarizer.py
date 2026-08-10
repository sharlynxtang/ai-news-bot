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
from ..collectors.base import date_label_cn

NewsItem = Dict[str, Any]

# ── constants ──────────────────────────────────────────────────────────────
BATCH_SIZE = 30          # items per first-pass LLM call
FIRST_PASS_TARGET = 6    # items to keep per batch in first pass (6×5 batches = 30 max)
MAX_FINAL_CANDIDATES = 28  # hard cap on candidates fed to final pass
FINAL_MIN = 15           # minimum items in final digest
FINAL_MAX = 25           # maximum items in final digest
SUMMARY_MAX_LEN = 300    # chars; longer summaries are truncated before sending

# Category display labels (order is preserved in the final Markdown).
# Section order (per product spec):
#   1. AI 产品与工具
#   2. AI Coding 竞品动态
#   3. AI Agent 竞品动态
#   4. 📑 论文速览
#   5. 数据与 AI 合规监管
# 「今日焦点」and「筛选说明」are rendered separately after these sections.
CATEGORY_LABELS: Dict[str, str] = {
    "product":            "AI 产品与工具",
    "competitor_coding":  "AI Coding 竞品动态",
    "competitor_agent":   "AI Agent 竞品动态",
    "paper":              "📑 论文速览",
    "policy":             "数据与 AI 合规监管",
}

# The "other" bucket is rendered after the four main sections only when
# it actually contains items.
OTHER_LABEL = "其他动态"

# Human-readable source labels used in fallback mode
SOURCE_CN: Dict[str, str] = {
    "techcrunch":  "TechCrunch",
    "theverge":    "The Verge",
    "arstechnica": "Ars Technica",
    "jiqizhixin":  "机器之心",
    "hackernews":  "Hacker News",
    "arxiv":       "arXiv",
    # Chinese sources
    "qbitai":      "量子位",
    "leiphone":    "雷锋网",
    "infoq_ai":    "InfoQ AI",
    "atyun":       "AI科技评论",
    # Competitor blog sources
    "github_blog_ai": "GitHub Blog",
    "vercel_blog":    "Vercel Blog",
    "replit_blog":    "Replit Blog",
    "langchain_blog": "LangChain Blog",
    "cursor_blog":    "Cursor Blog",
    "codeium_blog":   "Codeium Blog",
}

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


def _repair_json(text: str) -> Optional[str]:
    """Best-effort repair of a JSON string truncated mid-output.

    Strategy: find the last fully-closed item object (``}``), then close the
    ``items`` array and append a placeholder ``focus_analysis``.  Returns
    *None* when the text doesn't look like our expected schema at all.
    """
    # Drop anything after the last complete closing brace
    last_brace = text.rfind("}")
    if last_brace == -1:
        return None
    truncated = text[: last_brace + 1]
    if '"items"' not in truncated:
        return None
    # Close items array + top-level object
    repaired = truncated + '], "focus_analysis": "（今日焦点分析因输出截断而省略）"}'
    try:
        json.loads(repaired)
        return repaired
    except json.JSONDecodeError:
        return None


def _parse_json_response(text: str) -> Dict[str, Any]:
    cleaned = _strip_code_fence(text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        repaired = _repair_json(cleaned)
        if repaired is not None:
            _log("[summarizer] JSON was truncated — repaired and continuing")
            return json.loads(repaired)
        raise


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
                "请用中文输出所有摘要和分析；英文标题保留原文，摘要和分析必须是中文。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"以下是 {len(items)} 条 AI 新闻。请选出最重要的 {target} 条，"
                "并为每条生成 1-2 句精炼中文摘要。\n\n"
                "选择标准：\n"
                "1. 优先选择有实质性进展的新闻（重大产品发布/更新、竞品动态、突破性论文、数据/AI合规监管）\n"
                "2. 去重：同一事件的多条报道只保留最重要的一条\n"
                "3. 校正 category，可选值："
                "product / competitor_coding / competitor_agent / paper / policy / other\n"
                "   - product（AI 产品与工具）：新 AI 产品/工具发布、现有产品重大更新（ChatGPT/Claude 新功能）、开发者工具与 SDK 更新\n"
                "   - competitor_coding（AI Coding 竞品）：Cursor、Windsurf/Codeium、GitHub Copilot、Claude Code、Codex CLI、Augment Code、Cline、Devin、Amazon Q、JetBrains AI、Tabnine、Replit Agent、Qoder 等编程工具动态\n"
                "   - competitor_agent（AI Agent 竞品）：Manus、Lovable、v0/Vercel、Bolt.new、Coze、Dify、LangChain、CrewAI、AutoGPT、n8n、千问办公 等 Agent/应用构建平台，以及 AI+垂直行业（Harvey AI 法律、HireVue HR、Kensho 金融）\n"
                "   - paper（论文速览）：arXiv 等新论文，仅需 1 句摘要说明主题\n"
                "   - policy（数据与 AI 合规监管）：仅限数据合规（GDPR、个人信息保护、数据跨境）、AI 治理/监管（EU AI Act、算法备案、AI 伦理）、AI 训练数据版权。**不含**一般网络安全漏洞/黑客攻击\n"
                "   - 注意：不再设融资/收购分类，纯融资新闻若与竞品无关请归入 product 或不选\n\n"
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
            # Precomputed date label (local SGT), e.g. "（8月9日）".
            "date_label": date_label_cn(it.get("published_at")),
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
                "请用中文输出所有摘要和分析；英文标题保留原文，摘要（summary_cn）和今日焦点（focus_analysis）必须是中文。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"以下是 {len(items)} 条候选 AI 新闻。请完成以下任务：\n\n"
                f"1. 筛选：选出最重要的 {FINAL_MIN}–{FINAL_MAX} 条\n"
                "2. 去重：同一事件的不同来源报道合并为一条（保留最有价值的 URL）\n"
                "3. 分类校正：确认/修正 category，可选值："
                "product / competitor_coding / competitor_agent / paper / policy / other\n"
                "   - product（AI 产品与工具）：新 AI 产品/工具发布、现有产品重大更新、开发者工具与 SDK 更新；每条尽量包含 产品名 + 具体更新内容 + 影响\n"
                "   - competitor_coding（AI Coding 竞品动态）：Cursor、Windsurf/Codeium、GitHub Copilot、Claude Code、Codex CLI、Augment Code、Cline、Devin、Amazon Q、JetBrains AI、Tabnine、Replit Agent、Qoder 等编程工具的版本发布/新功能/定价/性能对比\n"
                "   - competitor_agent（AI Agent 竞品动态）：Manus、Lovable、v0/Vercel、Bolt.new、Coze、Dify、LangChain、CrewAI、AutoGPT、n8n、千问办公 等 Agent/应用构建平台，以及 AI+垂直行业应用（Harvey AI 法律、HireVue HR、Kensho 金融）\n"
                "   - paper（论文速览）：新论文，每条仅需 1 句摘要说明主题，无需深度解读\n"
                "   - policy（数据与 AI 合规监管）：仅限数据合规、AI 治理/监管、AI 训练数据版权；**排除**一般网络安全漏洞/黑客攻击\n"
                "   - 无融资/收购分类：纯资本市场新闻若与竞品无关请归入 product 或不选\n"
                "4. 中文摘要：每条 1-2 句精炼中文摘要（论文仅 1 句；英文原文需翻译）\n"
                "5. 日期标注：每条保留输入中的 date_label（形如「（8月9日）」）原样输出，不要修改或删除\n"
                "6. 今日焦点：2-3 句综合分析，指出当日最值得关注的趋势或事件\n\n"
                f"新闻列表：\n{items_json}\n\n"
                "请以纯 JSON 格式返回（不要包含 ```代码块``` 标记）：\n"
                '{"items":[{"title":"<标题>","url":"<url>","summary_cn":"<中文摘要>",'
                '"date_label":"<原样保留的日期标注>","category":"<category>"}],'
                '"focus_analysis":"<今日焦点>"}'
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

    def summarize(
        self,
        items: List[NewsItem],
        stats: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Return a Markdown-formatted daily digest for *items*.

        Parameters
        ----------
        items:
            De-duplicated news items from the collectors pipeline.
        stats:
            Optional collection statistics used to render the「筛选说明」
            (filtering-transparency) section. Recognized keys:
            ``sources`` (list[str]), ``total_collected`` (int),
            ``deduped`` (int). ``final`` is derived from the rendered digest.
        """
        if not items:
            return self._empty_report()

        # Treat missing or placeholder key as "no key"
        key_ok = bool(self.api_key) and not self.api_key.startswith("sk-your")
        use_llm = not self.dry_run and key_ok

        if not use_llm:
            reason = "dry-run mode" if self.dry_run else "no valid API key"
            _log(f"[summarizer] using template fallback ({reason})")
            return self._fallback_format(items, stats)

        try:
            return self._llm_pipeline(items, stats)
        except Exception as exc:
            _log(f"[summarizer] LLM pipeline failed ({exc}), falling back to template")
            return self._fallback_format(items, stats)

    # ── LLM pipeline ─────────────────────────────────────────────────────

    def _llm_pipeline(
        self, items: List[NewsItem], stats: Optional[Dict[str, Any]] = None
    ) -> str:
        items = _preprocess(items)

        if len(items) > BATCH_SIZE:
            _log(f"[summarizer] {len(items)} items > {BATCH_SIZE}, running multi-batch select")
            candidates = self._multi_batch_select(items)
        else:
            candidates = items

        # Hard cap: too many candidates bloat the final-pass prompt and risk
        # truncated JSON output. Sort by publication time (newest first) so we
        # keep the most recent items when trimming.
        if len(candidates) > MAX_FINAL_CANDIDATES:
            candidates.sort(
                key=lambda x: x.get("published_at", ""), reverse=True
            )
            candidates = candidates[:MAX_FINAL_CANDIDATES]
            _log(f"[summarizer] trimmed candidates to {MAX_FINAL_CANDIDATES}")

        _log(
            f"[summarizer] final pass: {len(candidates)} candidates "
            f"→ selecting {FINAL_MIN}–{FINAL_MAX}"
        )
        result = self._call_final(candidates)
        return self._render_markdown(result, stats)

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
            "max_tokens": 8192,
        }
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=90)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except Exception as exc:
            _log(f"[summarizer] API call failed: {exc}")
            return None

    # ── fallback (no LLM) ────────────────────────────────────────────────

    def _fallback_format(
        self, items: List[NewsItem], stats: Optional[Dict[str, Any]] = None
    ) -> str:
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
                source = it.get("source", "")
                source_label = SOURCE_CN.get(source, source or "未知来源")
                cat_key = it.get("category", "other")
                cat_label = CATEGORY_LABELS.get(cat_key, OTHER_LABEL)
                raw = it.get("summary", "") or it.get("title", "")
                # Take a short English excerpt; append a Chinese source/category note
                # so even without LLM the user sees Chinese context.
                snippet = raw[:80].rstrip() + ("…" if len(raw) > 80 else "")
                summary_cn = f"（{source_label} · {cat_label}）{snippet}"
                digest_items.append(
                    {
                        "title": it.get("title", ""),
                        "url": it.get("url", ""),
                        "summary_cn": summary_cn,
                        "category": it.get("category", "other"),
                        # Carry the timestamp so _format_entry can date the bullet.
                        "published_at": it.get("published_at", ""),
                        "date_label": date_label_cn(it.get("published_at")),
                    }
                )

        focus = (
            f"（模板模式 · 未调用 AI 分析）当日共采集到 {len(items)} 条 AI 资讯，"
            "涵盖 AI 产品与工具、AI Coding 竞品、AI Agent 竞品、论文速览与数据/AI 合规监管等领域，请查阅各分类获取详情。"
        )
        return self._render_markdown(
            {"items": digest_items, "focus_analysis": focus}, stats
        )

    # ── Markdown renderer ─────────────────────────────────────────────────

    @staticmethod
    def _format_entry(it: Dict[str, Any]) -> str:
        """Render one bullet: ``• 标题：摘要（8月9日）[来源](url)``.

        The date label is taken from the item's ``date_label`` (echoed by the
        LLM). If absent — the LLM dropped it, or in fallback mode — it is
        recomputed from ``published_at`` so every entry is dated. Any trailing
        date label already embedded in the summary is not duplicated.
        """
        title = (it.get("title") or "").strip()
        summary = (it.get("summary_cn") or "").strip()
        url = (it.get("url") or "").strip()

        label = (it.get("date_label") or "").strip()
        if not label:
            label = date_label_cn(it.get("published_at"))
        # Avoid double-labelling if the summary already ends with the date.
        if label and summary.endswith(label):
            label = ""

        entry = f"• {title}：{summary}{label}"
        if url:
            entry += f" [来源]({url})"
        return entry

    def _render_markdown(
        self, result: Dict[str, Any], stats: Optional[Dict[str, Any]] = None
    ) -> str:
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
                    lines.append(self._format_entry(it))
            else:
                lines.append("• （暂无相关资讯）")
            lines.append("")

        # "Other" section only when it has content (not a required section)
        if by_cat["other"]:
            lines.append(f"━━ {OTHER_LABEL} ━━")
            for it in by_cat["other"]:
                lines.append(self._format_entry(it))
            lines.append("")

        # Focus section (always present)
        focus = (result.get("focus_analysis") or "").strip()
        lines.append("━━ 今日焦点 ━━")
        lines.append(focus if focus else "（暂无）")
        lines.append("")

        # Filtering-transparency section (筛选说明) — always present.
        final_count = len(result.get("items", []))
        lines.extend(self._render_filter_note(now_local, final_count, stats))

        return "\n".join(lines)

    def _render_filter_note(
        self,
        now_local: datetime,
        final_count: int,
        stats: Optional[Dict[str, Any]],
    ) -> List[str]:
        """Build the「筛选说明」lines exposing the collection funnel.

        Degrades gracefully: when *stats* is missing (e.g. summarizer called
        directly without pipeline stats) numeric fields fall back to what we
        can infer and unknown values are shown as "—".
        """
        stats = stats or {}
        # Strict SGT window (previous day 10:30 → today 10:30; Mon covers the
        # weekend). Displayed in local time with the configured timezone label.
        tz = config._local_tz()
        start_utc, end_utc = config.get_time_window()
        start_local = start_utc.astimezone(tz)
        end_local = end_utc.astimezone(tz)
        lookback = config.window_lookback_hours()
        tz_label = getattr(config, "TIMEZONE", "Asia/Singapore")
        start_s = start_local.strftime("%m-%d %H:%M")
        end_s = end_local.strftime("%m-%d %H:%M")

        raw_sources = stats.get("sources") or []
        source_labels = [SOURCE_CN.get(s, s) for s in raw_sources]
        n_sources = len(source_labels)
        source_list = "、".join(source_labels) if source_labels else "—"

        total = stats.get("total_collected")
        deduped = stats.get("deduped")

        def _fmt(v: Any) -> str:
            return str(v) if isinstance(v, int) else "—"

        # Filtered-out count = collected − deduped (best-effort; never negative).
        if isinstance(total, int) and isinstance(deduped, int):
            filtered = max(total - deduped, 0)
            filtered_s = str(filtered)
        else:
            filtered_s = "—"

        lines = ["━━ 筛选说明 ━━"]
        lines.append(
            f"• 时效窗口：{start_s} → {end_s}（{tz_label} · 过去 {lookback} 小时）"
        )
        lines.append(f"• 数据来源：共 {n_sources} 个源（{source_list}）")
        lines.append(
            f"• 采集量：{_fmt(total)} 条 → 去重后 {_fmt(deduped)} 条 "
            f"→ AI 精选 {final_count} 条"
        )
        lines.append("• 分类规则：基于关键词匹配 + LLM 智能分类")
        lines.append(f"• 剔除说明：{filtered_s} 条因相关性不足或重复被过滤")
        lines.append("")
        return lines

    def _empty_report(self) -> str:
        now_local = datetime.now(timezone.utc).astimezone()
        date_str = now_local.strftime("%Y-%m-%d")
        weekday_str = WEEKDAY_CN[now_local.weekday()]
        return (
            f"📊 每日 AI 资讯早报 | {date_str} {weekday_str}\n\n"
            "（今日未采集到任何 AI 资讯）\n"
        )
