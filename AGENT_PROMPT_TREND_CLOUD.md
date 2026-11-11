# 云端 Steam 评论词频与趋势分析 Agent 提示词

请接手本项目的词频与趋势分析模块。先阅读 [云端接入指南](README_NEW.md) 和你所在分支已有的分析代码，然后用组内云端 API 读取真实评论，实现或接入按游戏独立运行、可断点续读的词频和趋势分析。保留已有本地采集代码和 README。

## 数据入口与凭据

- 数据 API 基址为 `https://43.161.240.54`。程序通过 `/v1/` 接口读取 JSON。完整请求参数和响应结构见 [API 文档](https://43.161.240.54/docs)。
- [采集监控页面](https://43.161.240.54/monitor/)供人工查看三款游戏的采集状态和评论。分析输入统一来自数据 API。
- 从运行环境读取 `STEAM_API_USER`、`STEAM_API_PASSWORD`，对云端 HTTPS API 使用 HTTP Basic Auth。由组员私下提供真实凭据，缺少配置时提示组员设置。凭据保存在运行端，代码、Git、日志、URL、前端页面和回复中均省略真实值。
- 先请求 `GET /v1/games` 和 `GET /v1/health?app_id=730`，确认游戏已启用，服务可以提供数据。HTTP `401` 或 `403` 时检查认证，连接失败时明确报告数据未取得。

| 游戏 | `app_id` |
| --- | ---: |
| CS2 | `730` |
| Dota 2 | `570` |
| 影之刃零（Phantom Blade Zero） | `4115450` |

游戏目录之外的请求显式指定 `app_id`。本地保存的评论、`stream_id`、增量游标和趋势结果均按游戏隔离。评论主键用 `(app_id, review_id)`，`review_id` 保持字符串。

## 首次加载与持续更新

1. 对所选游戏请求 `GET /v1/reviews?app_id=730&after_id=0&limit=500`，保存**第一页**的 `stream_id` 和 `snapshot_cursor`。按响应的 `next_after_id` 逐页读取，直到 `has_more` 为 `false`。每页核对 `stream_id` 和每条评论的 `app_id`。先完整接收评论，再根据 `usable_for_trend` 做分析筛选。
2. 从**第一页**的 `snapshot_cursor` 开始调用 `GET /v1/reviews/changes?app_id=730&cursor=<cursor>&limit=500`，补齐分页期间发生的变化。这个游标属于该游戏的数据流，每页的 `snapshot_cursor` 对应该页读取时的水位。
3. 增量响应的 `items` 是 `upsert` 事件，评论字段位于 `items[].review`。按事件顺序处理。新 ID 插入，已有 ID 仅在 `revision` 更高时更新，重复版本或较旧版本保持幂等。评论由可用变为不可用时移除原统计贡献。只有票数等互动字段变化时，词频贡献保持原值。
4. 一页的记录及衍生状态提交成功后保存该页的 `next_cursor`，同一事务提交最佳。若 `has_more` 为 `true`，立即读取下一页。之后定期用已保存的游标读增量。遇到请求失败时保留已成功提交的游标。收到 `409` 或发现 `stream_id` 改变时，核对数据源和 `app_id`，再按该流重新初始化。

没有当前评论快照时，也可从 `cursor=0` 重放全量增量历史。重放过程中可能读到历史清洗版本，按 `revision` 收敛到最新记录，核对清洗版本后发布结果。游标原样保存为字符串，`after_id` 使用接口返回的整数 `next_after_id`。

## 用户点击立即采集时

1. 对所选游戏发送 `POST /v1/collect`，请求头为 `Content-Type: application/json`，请求体例如 `{"app_id":730}`。HTTP `202` 表示任务已受理，保存返回的 `request_id`。
2. 用 `GET /v1/collect?app_id=730&request_id=<request_id>` 约每 2 秒查一次。`queued` 和 `running` 时继续等，设置合理超时。已运行的同游戏任务可能复用，按接口返回的任务 ID 跟踪即可。
3. `completed` 时读取 `result.inserted` 和 `result.updated`，然后从该游戏已保存的游标继续调用 `/v1/reviews/changes`。`inserted: 0` 表示本轮没有新增，`updated` 仍可能大于零。`result.sweep_complete: false` 表示还有分页待采集，后续继续读增量。
4. `failed` 时显示 `error`，查询该游戏的 `/v1/health` 获取状态。保留消费游标，后续继续读已落库的增量。HTTP `429` 按 `Retry-After` 秒数等待。`503` 表示采集暂不可用。旧任务 ID 在服务重启或被淘汰后可能返回 `404`，此时恢复正常增量读取。

即时采集只在用户主动请求或演示操作时触发。日常同步轮询 `GET /v1/reviews/changes`。云端常规采集在北京时间每小时 `00、10、20、30、40、50` 分调度，手动触发后仍沿用这些时点。网页按钮由自己的后端代理请求云端 API，浏览器仅与组员自己的后端通信。

## 词频与趋势输出

- 只统计 `usable_for_trend: true` 的最新评论版本。用 `tokens_trend` 统计已清洗的词项，保留重复词的次数。如果输出文档频率，每篇评论内先对词项去重。`text_raw`、`text_clean` 可用于人工核对。
- 用 `created_at` 的 UTC 发布时间入窗。以同一个 UTC 时刻 `T` 比较最近 1 小时 `[T-1h, T)` 与紧接其前的 24 小时 `[T-25h, T-1h)`。比较归一化词频或文档占比，记录两个窗口的分母、最低样本量和排序规则。评论修改仍归入原发布时间所在窗口。
- 按游戏输出热点词、近期和基线计数、趋势强度、窗口边界与数据更新时间。对无评论、样本不足或覆盖时段不完整的窗口明确标记。补到迟到评论或较新 `revision` 后，更新相应窗口的旧贡献和新贡献。
- 保持云端评论结构 `schema_version: "1.0"` 的兼容性。`votes_up`、`comment_count` 允许为 `null`。用至少一次真实只读请求验证响应格式，再用模拟数据验证重试、重复事件、评论修改、游戏隔离和零新增。报告实际验证过的游戏、样本数量、数据新鲜度及尚未验证的部分。
