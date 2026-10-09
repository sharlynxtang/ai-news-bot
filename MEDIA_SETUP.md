# 配图与 X 精选观点

早报优先提取 RSS 原文中的图片；X 帖子有图片时也会保留其图片链接。每条新闻仍附原文或原帖链接。无论图片上传是否成功，早报中的「原文配图」链接都可点击查看。

## 在飞书卡片中直接显示图片

飞书卡片的图片组件需要 `image_key`。仅有群机器人 Webhook 无法取得这个 Key，因此还需要一个飞书自建应用：

1. 在飞书开放平台创建自建应用，并开启机器人能力。
2. 给应用开通 **获取与上传图片或文件资源**（`im:resource`）或 **上传文件 V2**（`im:resource:upload`）权限，按平台要求发布或启用应用。
3. 将应用的 **App ID**、**App Secret** 分别保存为 GitHub Actions Repository secrets `FEISHU_APP_ID`、`FEISHU_APP_SECRET`。不要提交或聊天发送其值。原有的 `FEISHU_WEBHOOK_URL` 继续用于发卡片。
4. 手动运行一次工作流，在日志中查看 `[pusher] embedded N/M source images`，并在飞书确认图片显示。无原图、下载失败或权限不足时，消息仍保留图片链接。

程序最多上传三张图片，每张不超过 5 MB。它只下载公开 HTTPS 图片，拒绝本地地址和重定向；来源图片不符合要求时会保留链接。

## 加入 X 帖子

1. 申请可调用 X 官方 **recent search** 接口的 API 访问，并取得只读 Bearer Token。可用套餐和额度以 X 当前开发者后台为准。
2. 把令牌保存为 GitHub Actions Repository secret `X_BEARER_TOKEN`。不要把令牌放在 URL、仓库或聊天中。
3. 默认账号名单见 `.env.example` 的 `X_ACCOUNTS`；可在运行环境覆盖为逗号分隔的用户名。`X_MAX_POSTS` 默认 4。程序筛选早报时效窗口内的原创 AI 帖子，去掉转帖、回复和无关内容，保留原帖链接。
4. 手动运行工作流并检查「X 精选观点」板块。没有令牌或 API 权限时，其他新闻源照常运行，日志会显示 X 采集被跳过或请求失败。

飞书图片上传接口和[图片组件](https://open.feishu.cn/document/feishu-cards/card-components/content-components/image)均使用飞书官方能力。
