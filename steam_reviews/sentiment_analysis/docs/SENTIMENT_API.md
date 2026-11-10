# 情绪分析结果 API（给 dashboard 组员）

本接口只读，运行在 `127.0.0.1:8100`，数据来自本机采集服务（`127.0.0.1:8000`）和 `sentiment_analysis` 的分析库。dashboard 不要直接读采集库或分析 SQLite，只轮询这个 API。

## 启动顺序

四个进程可以同时跑，建议分别开终端：

```powershell
cd steam_reviews

# 1. 采集/清洗服务（handoff 提供，8000）
.\start.bat

# 2. VADER 入口 + 增量消费（每游戏一个进程；持续运行）
.\.venv\Scripts\python.exe -m sentiment_analysis run --app-id 730 --consumer sentiment_algorithm_v1
.\.venv\Scripts\python.exe -m sentiment_analysis run --app-id 570 --consumer sentiment_algorithm_v1
.\.venv\Scripts\python.exe -m sentiment_analysis run --app-id 4115450 --consumer sentiment_algorithm_v1

# 3. LLM 增量补标（每 180 秒；可以先用 --once 测试一次）
.\.venv\Scripts\python.exe -m sentiment_analysis.llm_refresh --interval 180

# 4. 结果 API（给 dashboard）
.\.venv\Scripts\python.exe -m sentiment_analysis serve --port 8100
```

`llm_refresh` 只处理“还没有 LLM 标签”的 review×entity；没有新数据时不会调用 LLM。它内部调用 WSL 的 `run_cityu_llm.sh`，密钥只从 WSL `~/.env_secrets` 读取（`CITYUCS_LLM_*`，或回退到 `SENTIMENT_LLM_*`），不经过 API 或仓库。填 key 的步骤见仓库根目录 `QUICKSTART.md`。

## 数据语义

- 采集端每 **600 秒**轮询 Steam，所以这是 near-real-time，不是秒级。
- `run` 大约每分钟消费一次 changes，VADER 先写入。
- `llm_refresh` 每 180 秒补一次 LLM 标签；新评论在补标前只有 VADER。
- 推荐曲线桶：**1 小时**或 **10 分钟**；不要做秒级曲线（样本不足且采集有延迟）。
- 所有时间都是 UTC；`bucket_start`/`bucket_end` 是左闭右开区间。
- `scorer=llm` 时只返回 LLM 标签，不会把 VADER 画进曲线；`scorer=effective` 才使用 VADER fallback。
- `pending_llm_rows > 0` 表示还有新行没补 LLM；dashboard 可以显示“分析中”或暂时保留上一个点。
- `data_status`：`ok / llm_partial / vader_only / backfill_incomplete / stale_source / mixed_pipeline_versions / sample / no_data`。`insufficient_window` / `insufficient_baseline` 是实体级状态，表示样本不足，**不能当 0**。

## 接口

所有请求显式传 `app_id`：CS2=730、Dota 2=570、Phantom Blade Zero=4115450。

### 仪表盘情绪总览

```http
GET /v1/dashboard/overview?app_id=730&hours=24
```

此接口按一次性 `as_of` 快照返回任务4的情绪统计、分桶、最近小时与此前24小时的对比、负面占比提醒和最近评论样例。`hours` 仅接受 `6`、`24`、`168`，默认 `24`；快照截止时间向下对齐到 UTC 整点，保证桶完整。统计窗口均为左闭右开，评论按 `created_at` 归属，情绪采用 `effective`（优先LLM标签，否则使用VADER）。

响应字段与前端草案一致：`range`、`status`、`summary`、`buckets`、`comparison`、`alert`、`review_samples`。`status.data_state` 为 `ready`、`partial` 或 `pending`；覆盖范围未知的小时桶 `counts=null`，相应的 `summary=null`。提醒比较 `[T-1h,T)` 和 `[T-25h,T-1h)`，每边至少20条已分类评论、负面率上升至少15个百分点才是 `triggered`。数据未就绪时返回 `pending`，样本不足返回 `insufficient_data`，否则返回 `clear`。

**趋势词榜不属于情绪模块。** 目前此接口返回 `trends.status="pending"` 和空 `items`，直到趋势检测模块接入并提供真实结果；这不表示已经检测且没有突增词。总览 GET 只读已消费的分析库，不触发 Steam 采集。`GET /v1/games` 和 `/v1/health` 仍由采集服务8000提供；情绪总览由本结果服务8100提供。

### 游戏目录

```http
GET /v1/sentiment/games
```

返回每个游戏的可用性、实体数、分析量、LLM coverage、pending、data_status。

### 健康/覆盖

```http
GET /v1/sentiment/health?app_id=730
```

字段：

```text
analyzed_reviews, llm_labeled_reviews,
analysis_rows, llm_rows, pending_llm_rows, llm_coverage,
scorer_counts, processed_cursor, source_latest_cursor, cursor_lag,
backfill_status, last_success_at, last_error,
prompt_version, llm_model, mode, data_status
```

`health_source=collector` 表示刚从 8000 读到；`cached` 表示采集服务不可用，使用分析库缓存。

### 待补标数量

```http
GET /v1/sentiment/pending?app_id=730
```

dashboard 可用它判断是否要等 LLM 补标。

### 实体列表

```http
GET /v1/sentiment/entities?app_id=730
```

返回 `entity_id`、`name`、`scope`、`require_mention`；dashboard 不要硬编码实体。

### 当前窗口汇总

```http
GET /v1/sentiment/summary?app_id=730&window_hours=24&baseline_hours=168&scorer=llm
```

可选参数：`min_mentions=5`、`change_threshold=0.15`、`alpha=0.05`、`now=2026-11-09T12:00:00Z`、`scorer=llm|effective`。

每个实体包含：

```text
current: positive_count, negative_count, neutral_count, mention_count, metric
baseline: 同上
change, change_flag, alert_type, significance,
data_status, provisional, current_pipeline_versions
```

`metric = (positive - negative) / mention_count`；`mention_count=0` 时是 `null`。`alert_type`：`statistical`（卡方显著）或 `threshold`（阈值告警）或 `none`。

### 告警

```http
GET /v1/sentiment/alerts?app_id=730&window_hours=24&baseline_hours=168&scorer=llm
```

只返回 `change_flag=true` 或 `alert_type != none` 的实体，附带 `alert_count`。

### 时间序列（画曲线用）

```http
GET /v1/sentiment/series?app_id=730&entity_id=game&days=7&bucket_hours=1&scorer=llm
```

- `entity_id` 可选；省略时返回该游戏所有实体。
- `days` 1–90；`bucket_hours` 1–24。
- 每个 bucket：

```json
{
  "bucket_start": "2026-11-09T12:00:00Z",
  "bucket_end": "2026-11-09T13:00:00Z",
  "positive_count": 10,
  "negative_count": 2,
  "neutral_count": 3,
  "mention_count": 15,
  "net_metric": 0.533,
  "data_status": "ok"
}
```

`mention_count < min_mentions` 时 `data_status=insufficient_sample`；0 条时 `no_data`，`net_metric=null`。不要把这些点补成 0。

## 推荐轮询方式

```python
import json, time, urllib.request

BASE = "http://127.0.0.1:8100"
def get(path):
    with urllib.request.urlopen(BASE + path, timeout=10) as response:
        return json.load(response)

while True:
    series = get("/v1/sentiment/series?app_id=730&entity_id=game&days=7&bucket_hours=1&scorer=llm")
    points = series["entities"][0]["buckets"]
    pending = series["pending_llm_rows"]
    print(series["generated_at"], "pending_llm_rows=", pending)
    # TODO: 用 points 更新曲线；data_status != ok 的点跳过或显示为虚线
    time.sleep(60)
```

dashboard 组员只需要：

1. 先 `GET /games` 和 `GET /entities` 初始化游戏/实体选择器；
2. 每 30–60 秒拉一次 `/series` 和 `/alerts`；
3. 显示 `pending_llm_rows` 和 `llm_coverage`；
4. `data_status != ok` 时标注“不完整/分析中”，不要把缺失当 0。

## 错误码

- `404`：该游戏分析库不存在，先跑对应 `run` 消费者
- `422`：`app_id` 不支持、实体名未知、时间格式错误
- `503`：分析库未初始化，先运行消费者/`llm_refresh`

## 注意

- API 只读，不会触发采集或改写结果。
- 不要用 `steam_recommended` 当情感真值；它是平台推荐选择，不是文本情感。
- 多实体同句、反讽、混语、短文本仍是已知限制；`evidence/reason` 只用于审查，不是人工真值。
- 如果 API 返回 `stale_source`，优先检查 8000 采集服务是否还在运行。
