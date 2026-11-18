# Dashboard 统一总览接口

云端启动器会独立监控三个消费者及本地 API。单个进程退出时仅重启该进程，等待时间依次为2、4、8、16、32秒，最高60秒；稳定运行5分钟后重置等待时间。临时云端网络错误不会再导致其他消费者和整个本地 API 一起退出，重启消费者沿用原数据库和游标继续同步。Ctrl+C 仍会关闭整组服务。

同步显示约定：同一数据流存在正向游标差时，保留已提交快照的统计与高频词，标记 `status.data_state=partial`、`sync_in_progress=true` 并返回 `cursor_lag`、`processed_cursor`、`source_latest_cursor`。前端提示尚待同步，趋势和告警保持 pending；游标倒退、未知或数据流不一致仍隐藏不能信任的总计。同步完成后恢复检测。

分支：`feat/dashboard-overview`。在原情感总览接口上整合词频与趋势检测，保持前端 `API_SPEC.md` 的 `schema_version: "1.0"` 契约。

## 请求与启动

```http
GET /v1/dashboard/overview?app_id=730&hours=24
```

`app_id` 必填，可取 `730`、`570`、`4115450`。`hours` 可取 `6`、`24`、`168`，默认 `24`，其他值返回 422。

在 `steam_reviews` 中，沿用项目安装流程创建 `.venv` 并安装 `requirements.txt`、`sentiment_analysis/requirements-sentiment.txt`。先启动采集服务，再消费评论，最后启动结果 API：

```bash
# 离线联调，假设采集服务已经在 8000 启动；demo 消费者仅用于样例
.venv/bin/python -m sentiment_analysis run --all-games --consumer dashboard_demo --base-url http://127.0.0.1:8000 --once --no-summary
.venv/bin/python -m sentiment_analysis serve --consumer dashboard_demo --collector-url http://127.0.0.1:8000 --port 8100
```

前端可通过本服务的 `GET /v1/games` 认证代理读取采集目录；其余页面数据读取本总览接口，两者可使用同一地址。

## 腾讯云真实数据接入

采集地址为 `https://43.161.240.54`，使用 HTTP Basic Auth。情感消费者和总览服务的源健康检查共用认证客户端，从进程环境读取凭据，保持 HTTPS 证书验证；本地样例请求不附加云端凭据。

在当前终端配置组内私下提供的用户名和密码，然后从 `steam_reviews` 运行：

```bash
export STEAM_API_USER='团队用户名'
export STEAM_API_PASSWORD='团队密码'
.venv/bin/python -m sentiment_analysis.cloud --port 8220
```

此入口启动三款游戏各自的增量消费者，每60秒查询一次已入库变更，并在本机8220提供总览。真实数据保存在独立的 `tencent_cloud_live` 消费者中；首次同步会消费历史变更，未追平时总览显示 partial/pending，后续从已保存游标继续。按 Ctrl+C 停止本组进程。演示库不受影响。

只验证一次同步时也可单独执行：

```bash
.venv/bin/python -m sentiment_analysis run --all-games --consumer tencent_cloud_live --base-url https://43.161.240.54 --limit 1000 --once --no-summary
```

所有云端请求仅使用 GET，不触发 `POST /v1/collect`、不调用 LLM；当前分类使用 VADER，后续可独立补充 LLM 标签。不要在代码、命令参数、Git 文件或日志中写入真实密码；凭据只通过环境变量传递。

前端默认将 `DASHBOARD_COLLECTOR_URL` 和 `DASHBOARD_OVERVIEW_URL` 都设为 `http://127.0.0.1:8220`。`GET /v1/games` 由本服务携带认证代理请求腾讯云，凭据不进入前端。8210仍可作为独立演示总览使用，但切换真实数据时应使用8220。

## 完整页面演示数据

小样例不足以触发词云和告警时，可在 `steam_reviews` 目录生成独立模拟库：

```bash
.venv/bin/python -m sentiment_analysis.seed_dashboard_demo
.venv/bin/python -m sentiment_analysis serve --consumer dashboard_fixture_demo --collector-url "" --port 8210
```

每款游戏372条模拟评论：此前24小时288条、最近1小时60条，另有24条较早历史记录。评论经过真实清洗和 VADER 分类；数据量与负面突增是为演示构造的，不是实际 Steam 结论。响应保留 `status.mode=demo`，评论原文含 `[DEMO REVIEW]`。

生成器不请求外部服务，使用单独的 `dashboard_fixture_demo` 消费者，拒绝写入 live 模式数据库。重复运行会更新相同评论ID的修订，不重复累加条数。时间按生成时刻的 UTC 整点定位；跨小时后若要维持最近窗口效果，重新执行生成命令。前端游戏目录仍可从原采集服务读取；本演示总览使用自身缓存的合成数据状态，所以 `--collector-url ""` 必须保留。

## 返回内容

| 字段 | 类型 | 用途 |
| --- | --- | --- |
| `schema_version` | string | 固定 `1.0` |
| `app_id` | integer | 请求的游戏 |
| `as_of` | UTC datetime string | 全部统计的统一截止点 T |
| `range` | `{start_at,end_at}` | `[T-hours,T)` |
| `status` | object | `mode`、`data_state`、最近成功采集时间、分析更新时间和采集间隔 |
| `summary` | object / null | 所选范围的评论总数、已分类数和正/中/负计数 |
| `buckets` | array | 恰好 hours 个连续的一小时桶，字段为 `start_at`、`end_at`、`counts` |
| `comparison` | object | 当前 `[T-1h,T)` 与基准 `[T-25h,T-1h)` |
| `trends` | object | 突增词、窗口有效评论数和分析状态 |
| `alert` | object | 负面占比告警、两窗口计数及阈值 |
| `review_samples` | array | 当前窗口最新最多5条评论，包含原文、情绪标签和 Steam 推荐标记 |

时间统一为带 `Z` 的 UTC ISO 8601 字符串，前端转为 UTC+8；窗口全部左闭右开。计数是非负整数，未知传 null，不补成0。`summary` 等于所有桶计数之和；存在未知桶时为 null。7天范围会读取完整7天，较早评论不会因比较窗口仅25小时而被截掉。

## 同一快照生成全部统计

总览开启 SQLite 只读事务，读取情感消费者已保存的评论镜像、当前修订标签、游标和缓存健康状态。所有统计共享一份记录集合和向下对齐 UTC 整点的 T。词频直接读取镜像内 `tokens_trend`、`usable_for_trend`，无需另一个消费者库或独立 JSON 文件。

GET 不拉取评论、不调用 LLM，也不写分析数据库。它从现有分析快照即时汇总，持续更新数据仍需运行原情感消费者。评论修订或可用性撤回后，下一次请求按当前记录重算词频，旧贡献不会重复累加。

## 趋势词

```json
{
  "status": "ready",
  "recent_eligible_count": 20,
  "baseline_eligible_count": 30,
  "items": [
    {"term": "lag", "recent_review_count": 20, "baseline_review_count": 0}
  ]
}
```

同一评论内某词出现多次只计一次。沿用同学的默认算法：最近窗口至少4条有效评论、基准至少30条，某词近期至少2条评论提及，平滑强度至少2才入榜。强度为 `min(20, (recent_rate+0.05)/(baseline_rate+0.05))`。按现有算法强度排序并仅返回 `is_trending=true` 的前10项。

- `ready`：已经计算；空 items 表示没有符合规则的突增词。
- `insufficient_data`：当前或基准样本量不足，返回已知分母和空 items。
- `pending`：源数据未同步、覆盖不足、数据流不一致，或旧评论镜像缺少趋势可用性字段；未知分母为 null。

前端 Lift 仍由返回计数计算原始出现率倍率，与算法的平滑强度有不同含义。独立词云使用 `keywords` 中所选时间范围的评论提及数，详见末尾的高频词展示约定。

## 数据可用性与告警

真实模式会检查回填、游标、数据流身份、采集时间、清洗状态与25小时比较窗口覆盖。游标落后、倒退、未知或数据流不一致时，趋势保持 pending；无法确认覆盖的分桶返回 null。

告警还要求比较窗口内可用于情绪分析的评论已分类完成。两窗口各至少10条已分类评论，负面占比上升至少10个百分点时返回 `triggered`；样本不足为 `insufficient_data`；已计算未触发为 `clear`；尚未完整分析为 `pending`。页面选择7天但更早的覆盖不足，不会妨碍已完整覆盖的25小时比较。

离线数据始终返回 `status.mode="demo"`；没有真实最近成功采集时间时保持 `last_collection_at=null`。在已同步的完整合成快照上可计算演示结果，样本少则明确返回 insufficient_data，避免让页面一直表示排队等待。演示结果不能作为真实 Steam 结论。

## 来源与验证

- 情感基础：`origin/sentiment-analysis-dashboard`，提交 `7b4b090`。
- 词频、趋势：`origin/feat/person2-trend`，提交 `09d85fe`；沿用其 Person 1 的 `frequency.py`、`storage.py` 和 Person 2 的 `detector.py`，代码原样放入 `steam_reviews/trend_analysis/`。
- 新代码：同快照总览适配、窗口与状态衔接、7天统计修复和契约回归测试。

```bash
.venv/bin/python -m pytest tests sentiment_analysis/tests -q -p no:cacheprovider
```

新增测试覆盖三款游戏、6/24/168小时、共同截止点、词频去重、修订撤回、趋势入榜、样本不足、旧字段缺失、数据流/游标异常、覆盖未知、基准分类延迟与明确标记的演示结果。

## 高频词与趋势的展示补充

总览新增 `keywords: {status, eligible_count, items}`，items 最多50项，字段为 term、review_count，统计所选 hours 范围并按评论提及数排序，供独立词云使用。来源覆盖不明时返回 pending/null，确认为空时返回 ready/0/空列表。

新增 `trend_candidates` 列表，最多10个近期普通高频词，字段与 trends.items 一致。它独立于突增筛选，不改变 trends.status 或确认词列表；前端可在无突增或样本不足时标明普通词频参考。两组统计都来自同一只读快照。告警观察值保持返回，以便前端在不触发告警时也呈现样本量和比例。
