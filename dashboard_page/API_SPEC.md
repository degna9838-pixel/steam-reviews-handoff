# Steam Review Monitor 接口文档

状态：接入草案。本文区分交接包已实现的采集接口与待实现的仪表盘聚合接口。

## 1. 接口清单

交接包采集服务默认地址为 `http://127.0.0.1:8000`。下文的新增路由可以挂在同一服务，或由分析服务提供并在前端配置地址。接口使用 HTTP GET、UTF-8 JSON，请求无需 JSON 请求体。

| 方法与路径 | 状态 | 调用方与用途 |
| --- | --- | --- |
| `GET /v1/games` | 已有 | 仪表盘初始化时读取游戏目录及启用状态。 |
| `GET /v1/dashboard/overview?app_id=730&hours=24` | **待实现** | 仪表盘加载、切换筛选或自动刷新时获取一份完整快照。 |
| `GET /v1/health?app_id=730` | 已有 | 聚合服务读取采集状态。联调时也可直接调用排查。 |
| `GET /v1/reviews/changes?app_id=730&cursor=0&limit=100` | 已有 | 后端分析模块增量处理评论及其修订，按各游戏和消费者保存游标。 |
| `GET /v1/reviews?app_id=730&limit=5` | 已有 | 联调时抽查采集记录。 |
| `GET /v1/reviews/{review_id}?app_id=730` | 已有 | 联调时核对一条评论的原文及来源字段。 |

页面只需对接游戏目录与总览。底部评论区和 **View related reviews** 弹窗复用同一份总览中的 `review_samples`。点击按钮无需再次请求。增量拉取、去重、文本情绪分类和突增词检测由后端完成。

### 游戏目录字段

`GET /v1/games` 返回 `{schema_version, mode, games}`。目录接口不传 `app_id`。单款游戏的请求都显式传入 `app_id`，避免落入交接包默认的 CS2。

| 字段 | JSON 类型 | 用途 |
| --- | --- | --- |
| `schema_version` | string | 现有采集契约版本，当前为 `"1.0"`。 |
| `mode` | string | `live` 为真实采集，`offline` 为交接包样例。 |
| `games[].app_id` | integer | Steam 应用 ID，作为游戏筛选值。 |
| `games[].name` | string | 后端游戏名称，前端可按 ID 映射英文展示名。 |
| `games[].slug` | string | URL 友好的游戏标识。 |
| `games[].enabled` | boolean | 是否启用。禁用游戏不发起总览请求。 |

| app_id | 页面名称 |
| ---: | --- |
| 730 | Counter-Strike 2 |
| 570 | Dota 2 |
| 4115450 | Phantom Blade Zero |

### 采集与分析的衔接

- 现有 `/v1/health` 提供 `last_success_at`、`poll_seconds`、`mode`、`data_ready`、`backfill_status` 等字段。聚合服务应同时检查分析是否追到可发布的快照，不能单凭采集服务的 `ready` 判断情绪或趋势已就绪。
- 评论唯一键为 `(app_id, review_id)`。`review_id` 使用字符串，保留来源 ID。增量流按 `stream_id` 和游标区分，修订覆盖同一条评论的旧贡献。投票变化、重清洗或历史回填不会产生一条新的发布评论。
- `created_at` 是 Steam 评论发布时间，所有时间窗口据此分桶。采集观察时间和评论编辑时间用于排查或增量处理，不参与发布时间分桶。
- 趋势模块使用 `tokens_trend` 和 `usable_for_trend`。情绪模块使用 `text_clean` 和 `usable_for_sentiment`，在成功完成分类后写入 `positive`、`neutral` 或 `negative`。评论原文展示使用 `text_raw`。
- 后端在同一款游戏、同一数据流和同一发布快照上生成聚合结果。切换游戏不触发重新采集，也不会混合三款游戏的统计结果。游戏实体按评论所在 Steam 应用的 `app_id` 归属。

## 2. 总览接口（待实现）

```http
GET /v1/dashboard/overview?app_id=730&hours=6
Accept: application/json
```

| 参数 | 类型 | 必填 | 取值 |
| --- | --- | --- | --- |
| `app_id` | integer | 是 | `730`、`570`、`4115450`。 |
| `hours` | integer | 否 | `6`、`24`、`168`，默认 `24`。对应最近 6 小时、24 小时、7 天。 |

### 时间、数值与空值约定

- 时间字段统一传带 `Z` 的 UTC ISO 8601 字符串。前端转为 UTC+8 展示。所有窗口均为左闭右开 `[start_at, end_at)`。
- 后端每次发布结果确定统一截止时间 `as_of = T`，表示这份快照统计的时间上界。总览范围为 `[T − hours, T)`，按相对 T 的整小时连续分桶。趋势词、提醒与评论示例都使用同一个 T。
- 检测当前窗口为 `[T − 1 小时, T)`，基准窗口为 `[T − 25 小时, T − 1 小时)`，两个窗口不重叠。基准窗口可以超出页面选中的 6 小时范围。
- 所有计数为非负 JSON 整数。确认为零用 `0`，未知或尚未计算用 `null`。没有结果项用空数组。前端根据分子、分母计算比例和倍数，展示时才四舍五入。

### 顶层字段

下表字段均须返回。标记可空的字段允许 `null`。

| 字段 | JSON 类型 | 含义 |
| --- | --- | --- |
| `schema_version` | string | 总览契约版本，本文定义为 `"1.0"`，与采集契约分别维护。 |
| `app_id` | integer | 与请求匹配的 Steam 应用 ID。 |
| `as_of` | datetime | 本次快照的时间截止点，即 T。 |
| `range` | Window | 页面所选统计范围。 |
| `status` | Status | 数据模式、可用性及更新时间。 |
| `summary` | Counts 或 null | 所选范围的总计，用于前两张指标卡与情绪分布。 |
| `buckets` | array[Bucket] | 按时间升序的一小时分桶，用于堆叠趋势图。 |
| `comparison` | object | 包含 `recent` 和 `baseline` 两个 Window。 |
| `trends` | Trends | 突增词榜数据和可用状态。 |
| `alert` | Alert | 最新检测结果。当前界面每次展示 0 或 1 条提醒。 |
| `review_samples` | array[ReviewSample] | 所选游戏当前检测窗口内的评论示例，按发布时间降序，最多 5 条。 |

### Window、Status、Bucket 和 Counts

| 对象 | 字段 | JSON 类型 | 口径 |
| --- | --- | --- | --- |
| Window | `start_at`、`end_at` | datetime | 起点和排除的终点。 |
| Status | `mode` | string | `live` 或 `demo`。将上游 `offline` 样例映射为 `demo`。 |
| Status | `data_state` | string | `ready`、`partial`、`pending`。回填未完成或快照部分可用时用 `partial`，尚无可用结果时用 `pending`。 |
| Status | `last_collection_at` | datetime 或 null | 最近成功采集时间，可取采集服务 `last_success_at`。即便本轮新增 0 条，成功采集时间仍可更新。 |
| Status | `analysis_updated_at` | datetime 或 null | 当前分析快照成功发布的时间。 |
| Status | `collection_interval_seconds` | integer 或 null | 上游采集目标间隔，可取 `poll_seconds`。前端刷新间隔由前端单独配置。 |
| Bucket | `start_at`、`end_at` | datetime | 一小时窗口的边界。 |
| Bucket | `counts` | Counts 或 null | 当前桶的评论及情绪计数。未覆盖的桶传 `null`。 |
| Counts | `review_count` | integer | 采集到的该游戏英文评论数，含未分类评论。 |
| Counts | `classified_count` | integer | 成功分类为三类之一的评论数。 |
| Counts | `positive_count`、`neutral_count`、`negative_count` | integer | 三类情绪计数。三者相加等于 `classified_count`，且 `classified_count <= review_count`。 |

`buckets` 应包含恰好 `hours` 个小时桶，覆盖 `range` 且连续无重叠。确认为空的桶五项计数均为 0，前端将该小时情绪比例显示为无数据。只要存在计数未知的桶，`summary` 就传 `null`。其余情况下，`summary` 的每一项等于各桶对应计数之和。总负面占比用总负面计数除以总已分类计数，不能对每小时的百分比简单平均。

### Trends

| 字段 | JSON 类型 | 口径 |
| --- | --- | --- |
| `status` | string | `ready`、`insufficient_data` 或 `pending`。 |
| `recent_eligible_count`、`baseline_eligible_count` | integer 或 null | 各窗口纳入趋势分析的有效评论数。 |
| `items` | array[object] | 后端已筛选和排序的突增词，按展示顺序返回，建议最多 10 个。 |
| `items[].term` | string | 去停用词及规范化后的展示词。 |
| `items[].recent_review_count`、`items[].baseline_review_count` | integer | 各窗口提到该词的有效评论数。同一评论多次出现只计一次，计数不超过该窗口有效评论总数。 |

两个窗口的出现率分别等于「提到词的有效评论数 ÷ 该窗口有效评论数」。`Lift = 当前出现率 ÷ 基准出现率`。基准出现率为 0、当前出现率大于 0 时，前端显示 `New`。任一窗口有效评论数为 0 或样本量未达到后端规则时，使用 `insufficient_data` 与空列表。状态为 `ready` 且列表为空时，表示本轮没有符合规则的词。指标卡计数为 `items.length`，即展示出的词数。

### Alert

| 字段 | JSON 类型 | 口径 |
| --- | --- | --- |
| `status` | string | `triggered` 已触发、`clear` 已计算未触发、`insufficient_data` 样本不足、`pending` 尚未计算。 |
| `recent`、`baseline` | object 或 null | 各窗口情绪检测的样本计数。未知时为 `null`。 |
| `recent.classified_count`、`baseline.classified_count` | integer | 各窗口已分类评论数。 |
| `recent.negative_count`、`baseline.negative_count` | integer | 各窗口负面评论数。 |
| `min_classified_per_window` | integer | 每个窗口的最低已分类样本量，当前演示值为 `20`。 |
| `min_increase_pp` | number | 触发所需的最小上升幅度，单位为百分点，当前演示值为 `15`。 |

首版建议沿用演示规则。两个窗口分别达到最小样本量，且 `(当前负面占比 − 基准负面占比) × 100` 达到阈值时，由后端标为 `triggered`。前端用原始计数展示近似比例及百分点差，提醒标记定位到 `comparison.recent.end_at`。指标卡在 `triggered` 时显示 `1`，`clear` 时显示 `0`，未完成或样本不足时显示 `—`。当前窗口与末尾小时桶一致，已分类数和负面计数应相同。

### ReviewSample

| 字段 | JSON 类型 | 口径 |
| --- | --- | --- |
| `app_id` | integer | 必须等于响应的 `app_id`。 |
| `review_id` | string | 稳定评论 ID。 |
| `created_at` | datetime | 发布时间，须位于 `comparison.recent` 内。 |
| `text_raw` | string | 评论原文。前端按纯文本渲染。 |
| `sentiment` | string 或 null | `positive`、`neutral`、`negative`，未分类时传 `null`。 |
| `steam_recommended` | boolean | Steam 用户是否推荐游戏，与 `sentiment` 分开显示。 |

每条样例都应来自同一快照，按 `review_id` 去重。弹窗和底部样例区显示其实际条数及时间。无样例时用空数组。当前页面只需要上表字段，作者头像、昵称、清洗结果、点赞数和回复数无需纳入总览。

## 3. 完整响应示例

下面是 `app_id=730&hours=6` 的虚构快照。为缩短示例选择 6 小时，因此列出了完整的 6 个桶。两个评论 ID 仅用于演示。

```json
{
  "schema_version": "1.0",
  "app_id": 730,
  "as_of": "2026-11-09T12:00:00Z",
  "range": {"start_at": "2026-11-09T06:00:00Z", "end_at": "2026-11-09T12:00:00Z"},
  "status": {
    "mode": "demo",
    "data_state": "ready",
    "last_collection_at": "2026-11-09T12:00:00Z",
    "analysis_updated_at": "2026-11-09T12:02:00Z",
    "collection_interval_seconds": 600
  },
  "summary": {
    "review_count": 420,
    "classified_count": 394,
    "positive_count": 144,
    "neutral_count": 67,
    "negative_count": 183
  },
  "buckets": [
    {"start_at": "2026-11-09T06:00:00Z", "end_at": "2026-11-09T07:00:00Z",
     "counts": {"review_count": 48, "classified_count": 45, "positive_count": 21, "neutral_count": 9, "negative_count": 15}},
    {"start_at": "2026-11-09T07:00:00Z", "end_at": "2026-11-09T08:00:00Z",
     "counts": {"review_count": 48, "classified_count": 45, "positive_count": 20, "neutral_count": 9, "negative_count": 16}},
    {"start_at": "2026-11-09T08:00:00Z", "end_at": "2026-11-09T09:00:00Z",
     "counts": {"review_count": 48, "classified_count": 45, "positive_count": 19, "neutral_count": 8, "negative_count": 18}},
    {"start_at": "2026-11-09T09:00:00Z", "end_at": "2026-11-09T10:00:00Z",
     "counts": {"review_count": 48, "classified_count": 45, "positive_count": 21, "neutral_count": 8, "negative_count": 16}},
    {"start_at": "2026-11-09T10:00:00Z", "end_at": "2026-11-09T11:00:00Z",
     "counts": {"review_count": 48, "classified_count": 45, "positive_count": 20, "neutral_count": 8, "negative_count": 17}},
    {"start_at": "2026-11-09T11:00:00Z", "end_at": "2026-11-09T12:00:00Z",
     "counts": {"review_count": 180, "classified_count": 169, "positive_count": 43, "neutral_count": 25, "negative_count": 101}}
  ],
  "comparison": {
    "recent": {"start_at": "2026-11-09T11:00:00Z", "end_at": "2026-11-09T12:00:00Z"},
    "baseline": {"start_at": "2026-11-08T11:00:00Z", "end_at": "2026-11-09T11:00:00Z"}
  },
  "trends": {
    "status": "ready",
    "recent_eligible_count": 175,
    "baseline_eligible_count": 1000,
    "items": [
      {"term": "anticheat", "recent_review_count": 42, "baseline_review_count": 110},
      {"term": "crash", "recent_review_count": 25, "baseline_review_count": 70},
      {"term": "matchmaking", "recent_review_count": 21, "baseline_review_count": 60},
      {"term": "server", "recent_review_count": 17, "baseline_review_count": 50}
    ]
  },
  "alert": {
    "status": "triggered",
    "recent": {"classified_count": 169, "negative_count": 101},
    "baseline": {"classified_count": 1071, "negative_count": 396},
    "min_classified_per_window": 20,
    "min_increase_pp": 15
  },
  "review_samples": [
    {
      "app_id": 730,
      "review_id": "123450002",
      "created_at": "2026-11-09T11:46:00Z",
      "text_raw": "VAC needs work. I keep running into cheaters.",
      "sentiment": "negative",
      "steam_recommended": false
    },
    {
      "app_id": 730,
      "review_id": "123450001",
      "created_at": "2026-11-09T11:22:00Z",
      "text_raw": "I still recommend CS2, but matchmaking has been slow since the update.",
      "sentiment": "negative",
      "steam_recommended": true
    }
  ]
}
```

这个响应在页面上的对应关系如下。

| 页面区块 | 示例显示 |
| --- | --- |
| 评论总量 | `420` |
| 负面占比 | `46%`，即 `183 / 394` 四舍五入。 |
| 突增关键词数 | `4`。例如 `anticheat` 的出现率为 `24% vs 11%`，Lift 为 `2.18×`。 |
| 情绪提醒数 | `1`。当前约 `60%`，基准约 `37%`，上升约 `23 pp`。 |
| 趋势图及分布 | 趋势图使用 6 个小时桶，分布使用全范围总计。 |
| 评论区及弹窗 | CS2 最近一小时的两条样例，UTC+8 为 `19:46` 和 `19:22`。 |

## 4. 错误处理与接入验收

| HTTP 状态 | 含义及页面处理 |
| --- | --- |
| `200` | 可返回一份快照。用响应中的状态区分数据就绪、回填中、样本不足或分析尚未生成。 |
| `422` | `app_id` 缺失、未知，或 `hours` 不在支持范围内。保留当前筛选并提示参数错误。 |
| `503` | 游戏未启用或依赖不可用。保留该游戏上一次成功加载的结果并标明时间，避免将异常当成 0。 |

新增总览接口的错误响应建议返回 `{"error":{"code":"GAME_DISABLED","message":"This game is not enabled"}}` 这样的 JSON。采集服务已有接口沿用自己的错误格式，例如 FastAPI 的 `detail`。前端应对超时和连接失败显示可读提示；展示旧快照时须按游戏及时间范围分别保存，不能用另一个游戏的数据填充。

验收时应核对：

1. 三个 `app_id` 和三档 `hours` 都可请求，响应的游戏和范围始终匹配。缩短或扩大页面范围只改变汇总和分桶，同一快照的检测结果保持一致。
2. 各桶情绪计数及总计一致，零评论与未知数据能区分。遇到零分母不显示伪造的 0%。
3. 提醒和关键词比较窗口不重叠。评论区及弹窗均使用同游戏、同最新小时的样例。
4. 自动刷新按前端设定周期请求总览，不重新触发采集。页头单独展示最近成功采集和分析更新时间，演示模式保留 **DEMO DATA** 标记。

参考资料来自仓库根目录的 [后端交接说明](../README.md) 及角色交付 ZIP 内的 `docs/API_GUIDE.md`、`docs/DATA_DICTIONARY.md`。
