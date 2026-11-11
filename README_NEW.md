# Steam 评论云端 API 接入指南

CS5481 项目第 3、4、5 步可从腾讯云读取 CS2、Dota 2 和《影之刃零》的英文评论。采集、清洗和存储由云端运行。组员可以直接用 HTTPS 接口，原有 [README](README.md) 和 ZIP 继续提供本地部署说明。

## 两个入口

| 用途 | 地址 |
| --- | --- |
| 浏览器查看采集状态、评论原文和清洗结果 | [采集监控](https://43.161.240.54/monitor/) |
| 分析程序拉取 JSON | API 基址 `https://43.161.240.54`，参见 [接口文档](https://43.161.240.54/docs) |

监控页面供人工查看。趋势、情感和仪表板程序接入下文的 `/v1/` 接口。两个入口均需登录。API 使用 HTTP Basic Auth，登录信息由项目成员私下提供。代码示例使用环境变量 `STEAM_API_USER` 和 `STEAM_API_PASSWORD`，分别填入收到的用户名和密码。自己的网页应由后端发起请求，凭据保存在后端。

## 先验证能读到数据

打开 [游戏目录 JSON](https://43.161.240.54/v1/games)，在浏览器登录框输入收到的凭据。然后打开 [CS2 评论示例](https://43.161.240.54/v1/reviews?app_id=730&limit=3)，能看到 `items` 即可开始接入。

| 游戏 | `app_id` |
| --- | ---: |
| Counter-Strike 2（CS2） | `730` |
| Dota 2 | `570` |
| Phantom Blade Zero（影之刃零） | `4115450` |

每次请求显式传入对应游戏的 `app_id`，目录接口除外。各游戏的评论、`stream_id`、增量游标和分析结果分别维护。

### Python 读取示例

在运行环境中配置上述两个变量。以下代码只使用 Python 标准库，适用于 macOS 和 Windows。

```python
import base64
import json
import os
from urllib.parse import urlencode
from urllib.request import Request, urlopen

base_url = "https://43.161.240.54"
credentials = f"{os.environ['STEAM_API_USER']}:{os.environ['STEAM_API_PASSWORD']}"
authorization = "Basic " + base64.b64encode(credentials.encode()).decode("ascii")


def api(path, *, query=None, body=None):
    url = base_url + path + ("?" + urlencode(query) if query else "")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = Request(url, data=data)
    request.add_unredirected_header("Authorization", authorization)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    with urlopen(request, timeout=15) as response:
        return json.load(response)


app_id = 730
page = api("/v1/reviews", query={"app_id": app_id, "limit": 3})
for review in page["items"]:
    print(review["review_id"], review["text_clean"], review["tokens_trend"])
```

## 首次读取与持续更新

| 接口 | 用途 |
| --- | --- |
| `GET /v1/games` | 获取游戏目录、是否启用、评论数量和回填状态 |
| `GET /v1/health?app_id=730` | 查看采集状态、最近成功时间和下一次计划时间 |
| `GET /v1/reviews?app_id=730&after_id=0&limit=100` | 分页读取当前评论 |
| `GET /v1/reviews/{review_id}?app_id=730` | 按游戏和评论 ID 查看一条当前记录 |
| `GET /v1/reviews/changes?app_id=730&cursor=0&limit=100` | 按游标读取新入库评论和已有评论的更新 |

1. 初始化时，从 `after_id=0` 开始读评论。保存**第一页**的 `snapshot_cursor` 和 `stream_id`，后续列表页用 `next_after_id` 继续，直到 `has_more=false`。如果同一轮分页中 `stream_id` 变化，重新初始化该游戏。
2. 当前快照读完后，从第一页保存的 `snapshot_cursor` 调用变更接口，补上分页期间发生的变化。之后从上次已提交的 `next_cursor` 继续读。每页成功处理并保存后再推进游标。
3. 用 `(app_id, review_id)` 标识评论，按 `revision` 更新已有记录，避免重复计数。变更事件的评论字段在 `items[].review`。服务返回游标冲突或 `stream_id` 变化时，核对数据流并重新初始化对应游戏。

评论 JSON 保留本地版的 `schema_version: "1.0"`。主要字段包括 `text_raw` 原文、`text_clean` 清洗正文、`tokens_trend` 趋势词项、`created_at` 发布时间、`usable_for_trend` 和 `usable_for_sentiment`。时间戳用 UTC。`votes_up` 为有用票数，`comment_count` 为回复数，`null` 表示未知。

## 立即采集

云端按北京时间每小时的 `00、10、20、30、40、50` 分自动采集。立即采集可用于手动操作或演示，下一次固定采集时间保持原计划。组员自己的前端如需按钮，可把下列流程接到后端。

1. 调用 `POST /v1/collect`，请求使用 `Content-Type: application/json`，例如 `{"app_id":730}`。HTTP `202` 表示请求已受理，响应包含 `request_id`。所选游戏已有一轮采集时，复用该轮任务。
2. 约每 2 秒调用 `GET /v1/collect?app_id=730&request_id=<request_id>`。状态为 `queued` 或 `running` 时继续等待，并为轮询设置超时。
3. 状态为 `completed` 时，`result.inserted` 为实际新增条数，`result.updated` 为更新条数。若 `inserted=0`，可以显示「暂无新评论」。即使没有新增，已有评论的更新仍可通过变更接口读取。`sweep_complete=false` 表示后台还有后续分页。
4. 状态为 `failed` 时查看 `error`，并通过该游戏的健康接口查看详情。HTTP `429` 表示手动采集处于短冷却期，按响应的 `Retry-After` 重试。服务重启后，旧任务 ID 可能返回 `404`。此前已入库的数据仍可通过增量接口读取。

在上面的 Python 示例中，可用下面两句发起并查询一次。查询仍在等待时，后续只重复 GET。

```python
job = api("/v1/collect", body={"app_id": app_id})
status = api("/v1/collect", query={"app_id": app_id, "request_id": job["request_id"]})
```

采集完成后，分析程序按自己已保存的游标读 `GET /v1/reviews/changes`。日常自动刷新只需要读取变更接口。三款游戏继续使用独立数据流，首次回填各自启动前 30 天的评论，已存历史数据持续保留。

## 词频分析交接

把 [词频分析 Agent 提示词](AGENT_PROMPT_TREND_CLOUD.md) 交给负责趋势词检测的同学或其 Agent。登录信息通过私下渠道提供。
