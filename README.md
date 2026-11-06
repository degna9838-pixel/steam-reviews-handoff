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
