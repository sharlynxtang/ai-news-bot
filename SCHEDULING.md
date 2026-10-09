# 每天 11:00（新加坡时间）推送

GitHub Actions 的内置 `schedule` 可能明显延迟，因此本仓库使用外部 HTTP 定时器调用 `workflow_dispatch`。选择支持每日定时、HTTPS POST、自定义请求头和 JSON 请求体的服务（例如 cron-job.org）。新加坡时间全年为 UTC+8。

## GitHub 准备

1. 在仓库 **Settings → Secrets and variables → Actions** 中配置 `FEISHU_WEBHOOK_URL`。`OPENAI_API_KEY` 用于 LLM 摘要；没有它时，程序会使用模板摘要。
   如需卡片内嵌原文配图，还需配置 `FEISHU_APP_ID` 和 `FEISHU_APP_SECRET`，并在飞书应用中启用机器人与 `im:resource` 或 `im:resource:upload` 权限。如需采集 X 帖子，配置 `X_BEARER_TOKEN`；对应的 X API 套餐必须能访问 recent search。
2. 创建仅限 `sharlynxtang/ai-news-bot` 仓库、授予 **Actions: Read and write** 权限的 GitHub fine-grained personal access token。将令牌只保存在定时服务的私密配置中，不要写进仓库或聊天。
3. 合并包含本说明和 `.github/workflows/daily-news.yml` 的变更。工作流只保留 `workflow_dispatch`，以免内置定时与外部定时重复推送。

## 定时服务请求

- 时间：每天 **Asia/Singapore 11:00**，或每天 **UTC 03:00**。
- 方法：`POST`
- URL：`https://api.github.com/repos/sharlynxtang/ai-news-bot/actions/workflows/daily-news.yml/dispatches`
- 请求体：`{"ref":"main"}`
- 请求头：
  - `Authorization: Bearer <fine-grained-PAT>`
  - `Accept: application/vnd.github+json`
  - `Content-Type: application/json`
  - `X-GitHub-Api-Version: 2022-11-28`

GitHub 返回 HTTP `204` 表示已接受触发请求，不代表推送成功。手动运行一次定时服务任务后，检查 GitHub Actions 中 `Run AI News Bot` 的结果，并确认飞书收到消息。任务失败时查看该步骤日志；不要把 Webhook 或令牌值复制到日志分享中。令牌过期前需要在定时服务中更新。
