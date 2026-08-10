# AI News Bot

Multi-source AI news collector. Gathers recent AI-related items from RSS feeds,
Hacker News, and arXiv, normalizes them into a single schema, filters out
non-AI noise, and emits a time-sorted JSON list.

This repository currently implements the **project scaffold + data collection**
stage. The `summarizer` and `pusher` modules are stubs to be filled in later.

## Project layout

```
ai-news-bot/
├── src/
│   ├── config.py            # centralized config (env-driven, sane defaults)
│   ├── collectors/
│   │   ├── base.py          # BaseCollector: retry/timeout HTTP, schema, helpers
│   │   ├── rss_collector.py # generic multi-feed RSS (incl. 机器之心 中文源)
│   │   ├── hackernews.py    # HN Algolia search API
│   │   ├── arxiv_collector.py
│   │   ├── __init__.py      # registry + collect_all() runner
│   │   └── __main__.py      # `python -m src.collectors`
│   ├── summarizer/          # LLM digest (stub)
│   └── pusher/              # Feishu webhook (stub)
├── main.py                  # orchestrator with --dry-run
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # optional; collection works without any keys
```

## Usage

Run collection and print the JSON news list:

```bash
# Either entry point works. JSON goes to stdout, stats to stderr.
python -m src.collectors --pretty
python main.py --dry-run --pretty
```

Pipe just the JSON (stats are on stderr):

```bash
python -m src.collectors > news.json
```

## Output schema

Each item is a dict:

```python
{
    "title": "…",
    "url": "https://…",
    "source": "techcrunch | theverge | arstechnica | jiqizhixin | hackernews | arxiv",
    "summary": "1-2 sentence summary",
    "category": "product | funding | paper | policy | other",
    "published_at": "2026-08-07T10:00:00Z"   # ISO-8601 UTC
}
```

## Data sources

| Source | Type | Notes |
|--------|------|-------|
| TechCrunch AI, The Verge AI, Ars Technica, 机器之心 | RSS | configured in `config.RSS_FEEDS` |
| Hacker News | Algolia API | past `LOOKBACK_HOURS`, `points >= HN_MIN_POINTS` |
| arXiv | Atom API | `cs.AI/cs.CL/cs.CV/cs.LG`, recent, abstract → summary |

Add or remove RSS feeds by editing `RSS_FEEDS` in `src/config.py`.

## Configuration

All settings live in `src/config.py` and can be overridden via environment
variables (or a `.env` file). Key knobs:

- `HTTP_TIMEOUT` (default 10s), `HTTP_RETRIES` (default 3)
- `LOOKBACK_HOURS` (default 24), `HN_MIN_POINTS` (default 50)
- `MAX_ITEMS_PER_SOURCE` (default 30)
- `OPENAI_API_KEY`, `FEISHU_WEBHOOK_URL` (for the future summarizer/pusher)

## Design notes

- **Fault isolation**: a failure in one source never aborts the others —
  handled both inside collectors and in `collect_all()`.
- **Retry + timeout**: every HTTP GET goes through `BaseCollector._request`
  with a 10s timeout and 3 retries with backoff.
- **Relevance filter**: RSS/HN items are keyword-filtered against `AI_KEYWORDS`
  (English + Chinese); arXiv is already AI-scoped by category.
- **Sorting/dedup**: results are de-duplicated by URL and sorted by
  `published_at` descending.

## 部署指南 (Deployment)

本项目通过 **GitHub Actions** 实现每天定时自动运行（北京时间 10:30 推送到飞书）。
配置文件位于 [`.github/workflows/daily-news.yml`](.github/workflows/daily-news.yml)。

### 1. Fork / Clone 仓库

将本仓库 Fork 到你自己的 GitHub 账号（或 Clone 后推送到你自己的新仓库）：

```bash
git clone <this-repo-url>
cd ai-news-bot
# 关联到你自己的远程仓库并推送
git remote set-url origin https://github.com/<your-name>/ai-news-bot.git
git push -u origin main
```

### 2. 配置 GitHub Secrets

进入你的仓库 **Settings → Secrets and variables → Actions → New repository secret**，
添加以下两个 secret：

| Secret 名称 | 说明 |
|-------------|------|
| `OPENAI_API_KEY` | OpenAI API key（用于 AI 整理摘要） |
| `FEISHU_WEBHOOK_URL` | 飞书自定义机器人的 Webhook URL |

> 注意：只需配置 secret，**不要**把真实密钥写进 `.env` 或提交到仓库。

### 3. 创建飞书自定义机器人

1. 在飞书中创建一个**只有自己的群**（方便接收测试消息）。
2. 打开该群 → **群设置 → 群机器人 → 添加机器人 → 自定义机器人**。
3. 填写机器人名称（如 `AI News Bot`），完成后**复制 Webhook URL**。
4. 把这个 URL 填入上一步的 `FEISHU_WEBHOOK_URL` secret。

### 4. 手动触发测试

进入仓库 **Actions → Daily AI News Report → Run workflow**，
选择 `main` 分支后点击 **Run workflow** 立即执行一次。

### 5. 验证

- 在 **Actions** 页面查看本次运行的日志，确认各步骤成功、`Run AI News Bot` 无致命报错。
- 检查你的飞书群是否收到了当日的 AI 新闻摘要卡片。
- 若未收到，先看 Actions 日志中 `main.py` 的 stderr 输出定位问题
  （常见：secret 未配置、Webhook URL 失效、OpenAI 额度/密钥问题）。

### 定时说明

workflow 的 cron 为 `30 2 * * *`（UTC 2:30），对应**北京时间 10:30**。
如需改时间，编辑 `daily-news.yml` 中的 `cron` 表达式（GitHub Actions 使用 UTC）。
单次运行设有 `timeout-minutes: 10` 上限，且脚本非零退出不会让 workflow 整体标红，
错误详情始终保留在日志中便于排查。
