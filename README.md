# Steam 游戏英文评论采集与清洗交付包

版本：1.3.0。供 CS5481 项目后续趋势词检测（原任务 3）和情感检测（原任务 4）使用。

## 下载对应的 ZIP

| 工作内容 | 下载 |
| --- | --- |
| 趋势词检测（原任务 3） | [steam_reviews_task1_trend.zip](steam_reviews_task1_trend.zip?raw=true) |
| 情感检测（原任务 4） | [steam_reviews_task2_sentiment.zip](steam_reviews_task2_sentiment.zip?raw=true) |

文件名 task1 / task2 指这两个下游工作角色。两份包的采集代码、依赖和启动脚本一致，只有 README.md 与 AGENT_PROMPT.md 不同。每人下载自己角色的一份即可。

## 给成员或 Agent 的入口

1. 下载并解压对应 ZIP，进入 steam_reviews 文件夹。两份 ZIP 解压目录同名，如需同时保留，请分别解压到不同文件夹。
2. 先阅读包内 README.md，按说明安装依赖并启动本地采集/API 服务。支持 Windows 和 macOS，推荐 Python 3.12（支持 3.11–3.14）。
3. 再阅读 AGENT_PROMPT.md、docs/API_GUIDE.md 与 docs/DATA_DICTIONARY.md，使用 examples/consume_api.py 接入增量数据，再开发对应算法。包内未实现趋势检测或情感分类算法。

可直接给 Agent：

> 请先解压对应 ZIP 并阅读 steam_reviews/README.md，再按其中指引阅读 AGENT_PROMPT.md 和 API 文档。完成安装、离线样例验证和真实采集/API 验证，然后开发该包对应的分析任务。所有请求显式传 app_id，三个游戏的状态、游标和分析结果分别保存。

## 游戏与数据隔离

| 游戏 | app_id |
| --- | ---: |
| Counter-Strike 2（CS2） | 730 |
| Dota 2 | 570 |
| Phantom Blade Zero（影之刃零） | 4115450 |

三个游戏分别采集、存储并维护独立增量流。API 请求必须显式传入对应 app_id；省略时默认 CS2。趋势和情感分析不能混合不同游戏的数据。

## 采集与 API 要点

- 仅采集英文评论。首次回填此前 30 天，完成追赶后每 600 秒开始下一轮检查；中断进度保存在本机 data 目录，保留该目录可继续采集。旧数据不会自动删除。
- API 返回 JSON，每条评论含 app_id、text_raw、text_clean、tokens_trend、时间、质量和可用性标记，以及 votes_up（有用票数）、comment_count（回复数）。
- 趋势任务使用 tokens_trend；情感任务使用 text_clean。增量处理使用 /v1/reviews/changes，并处理评论修改、撤回先前贡献及数据流身份变化，具体按包内文档实现。
- 互动计数是最近采到的快照；null 表示未知，不能当作 0。旧评论计数主要随每日全范围校对刷新，不保证每 10 分钟更新所有历史计数。
- 两包均含完整采集代码、安装/启动脚本、API 说明、样例和测试；不包含真实采集数据库、个人采集监视页面或已安装的虚拟环境。成员在自己的电脑上运行即可。

## ZIP 完整性校验（SHA-256）

```text
a16d5670df9d7b1e1300059767c2f7951d99bb784d6d1129c8e92d6d3e857b88  steam_reviews_task1_trend.zip
4465a130f9ba3a5677d4de4b97386e98c52736163d50cd6583be026b63d79db6  steam_reviews_task2_sentiment.zip
```

## 趋势词检测（Person 1）：数据集成与关键词频率分析

本模块基于上游 Steam 英文评论采集与清洗接口，实现三款游戏的独立增量同步、按 UTC 时间窗口查询，以及关键词频率统计，为后续 Trending Keywords 检测提供数据支持。

**交付文件：**
- [完整使用说明](person1_frequency/README_trend_01.md)
- [下载交接 ZIP](steam_reviews_person1_frequency_handoff.zip?raw=true)
- Python 源代码：`person1_frequency/trend_analysis/`
- 自动化测试：`person1_frequency/tests/`

**已实现功能：**
- 支持 CS2（730）、Dota 2（570）、影之刃零（4115450）。
- 复用上游 Consumer，实现增量同步及最新评论版本读取。
- 支持按游戏和 UTC 时间范围查询。
- 计算 TF（词频）、DF（评论提及数）、Document Rate（关键词提及率）及 Top-N 高频词。
- 提供同步状态检查，包括 Cursor、Stream ID、Backfill 和数据就绪状态。
- 已通过 24 个自动化测试，并使用真实 Steam 评论完成端到端验证。

**使用方法（Windows PowerShell）：**

先下载并解压上游 `steam_reviews_task1_trend.zip`，再将本模块 ZIP 的内容合并到其中的 `steam_reviews/` 目录。按照上游 README 安装依赖后，在该目录执行：

1. 启动上游 API：`.\start.bat`
2. 同步三款游戏：`.\.venv\Scripts\python.exe -m trend_analysis.consumer --consumer trend_live`
3. 运行测试：`.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_trend_*.py" -v`
4. 运行双窗口验收：`.\.venv\Scripts\python.exe .\trend_handoff_check.py`

**下游交接：** Person 2 可通过 `get_top_keywords(..., top_n=None)` 获取指定游戏、指定时间窗口的完整 TF/DF 数据，用于最近 1 小时与此前 24 小时的趋势比较。

## 趋势词检测（Person 2）：趋势判断与结果导出

本模块接在 Person 1 的词频接口之后。它比较最近 1 小时和再往前 24 小时的文档频率比例，判断突发趋势词，并导出看板可以直接读取的 JSON。采集、清洗和词频统计都没有改。

**交付文件：**
- [完整使用说明](person2_trend/README_trend_02.md)
- [下载交接 ZIP](steam_reviews_person2_trend_handoff.zip?raw=true)
- Python 源代码：`person2_trend/trend_analysis/`
- 自动化测试：`person2_trend/tests/`
- 看板字段样例：`person2_trend/analysis/experiments/dashboard_preview/trends_latest.json`（构造数据，不是 Steam 上的结论）

**已实现功能：**
- 支持 CS2（730）、Dota 2（570）、影之刃零（4115450），三款游戏分开计算。
- 最近 1 小时与再往前的 24 小时互不重叠，使用 Person 1 的 `get_top_keywords(..., top_n=None)`。
- 用文档频率比例计算趋势强度；基线里没有的词标成新词，强度保持有限。
- 最近评论太少、回填未完成或样例模式只保留分数，不公布趋势名单。
- 单次或定时导出 `analysis/trends_latest.json`。
- 构造样例中，突然变多的词会进入名单，一直很多的词不会进入。

**使用方法（Windows PowerShell）：**

先按上面的说明合并上游 ZIP 和 Person 1 的 ZIP，再把本模块 ZIP 的内容合并进同一个 `steam_reviews/` 目录。在该目录执行：

1. 启动上游 API：`.\start.bat`
2. 计算三款游戏的趋势：`.\.venv\Scripts\python.exe -m trend_analysis.run --once --consumer trend_live`
3. 运行测试：`.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_trend_*.py" -v`
4. 运行构造样例：`.\.venv\Scripts\python.exe -m trend_analysis.run --experiment`

**下游交接：** 看板读取 `analysis/trends_latest.json`。只有某个游戏的 `conclusions_publishable` 为 true 时，才展示该游戏的 `trending_terms`。

