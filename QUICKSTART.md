# 组员快速开始（情感分析 / 情绪曲线接口）

这个仓库包含 Steam 评论采集端和 `sentiment_analysis` 情感分析模块。拉下来之后**不能直接看到真实数据**，需要在本机跑采集服务；数据库、虚拟环境、LLM 原始输出都不在 git 里。

## 仓库里有什么

- `steam_reviews/`：采集/清洗代码、FastAPI、测试、启动脚本
- `steam_reviews/sentiment_analysis/`：实体情感、LLM 主检测、VADER fallback、变化检测、结果 API
- `steam_reviews/sentiment_analysis/README.md`：方法、命令、指标、限制
- `steam_reviews/sentiment_analysis/docs/SENTIMENT_API.md`：给 dashboard 组员的接口契约
- `steam_reviews/analysis/games/<app_id>/.../summaries/`：小体积汇总样例（可看格式，不是完整数据库）

## 不在仓库里

- `.venv/`、`data/`、`logs/`、`analysis/**/*.sqlite3`
- 逐条评论、LLM predictions JSONL、人工标注 CSV
- `~/.env_secrets` /任何 API key

所以每位组员要在自己电脑上跑采集，或者由组里指定一台机器做最终整合；不要直接拼接不同机器的 stream。

## 一次性安装

```powershell
cd steam_reviews
.\setup.bat
.\sentiment_analysis\setup_sentiment.bat
```

`setup.bat` 安装采集端依赖；`setup_sentiment.bat` 额外安装 `vaderSentiment` 和 pytest。

## 启动顺序（建议开多个终端）

```powershell
cd steam_reviews

# 1) 采集/API 服务（8000）——必须先启动
.\start.bat

# 2) 三游戏 VADER 入口（持续运行；每游戏一个进程）
.\.venv\Scripts\python.exe -m sentiment_analysis run --app-id 730 --consumer sentiment_algorithm_v1
.\.venv\Scripts\python.exe -m sentiment_analysis run --app-id 570 --consumer sentiment_algorithm_v1
.\.venv\Scripts\python.exe -m sentiment_analysis run --app-id 4115450 --consumer sentiment_algorithm_v1

# 3) LLM 增量补标（需要 WSL + CityU 密钥；没有密钥可先跳过）
.\sentiment_analysis\start_llm_refresh.bat

# 4) 给 dashboard 的结果 API（8100）
.\sentiment_analysis\start_api.bat
```

## 新环境先验证

```powershell
.\.venv\Scripts\python.exe -m cs2_reviews doctor
.\.venv\Scripts\python.exe -m pytest tests sentiment_analysis/tests -q
.\.venv\Scripts\python.exe -m sentiment_analysis doctor
```

API 启动后打开：

```text
http://127.0.0.1:8100/docs
http://127.0.0.1:8100/v1/sentiment/games
```

dashboard 主要接口：

```text
/v1/sentiment/games
/v1/sentiment/entities?app_id=730
/v1/sentiment/pending?app_id=730
/v1/sentiment/summary?app_id=730&window_hours=24&baseline_hours=168&scorer=llm
/v1/sentiment/alerts?app_id=730&window_hours=24&baseline_hours=168&scorer=llm
/v1/sentiment/series?app_id=730&entity_id=game&days=7&bucket_hours=1&scorer=llm
```

完整字段见 `steam_reviews/sentiment_analysis/docs/SENTIMENT_API.md`。

## 没有 CityU LLM 密钥怎么办

LLM 补标 worker 需要 WSL 里的 `~/.env_secrets` 配好 `CITYUCS_LLM_*`（模型 `CS5351/DeepSeek-V4-Flash`）。没有密钥时：

- 仍然可以跑采集 + VADER 入口 + 结果 API
- API 用 `scorer=effective` 才使用 VADER fallback；`scorer=llm` 会只返回已补标的 LLM 行
- `health` / `pending` 会显示 `data_status=vader_only` 或 `llm_partial`
- 不要伪造 LLM 标签，也不要把 VADER fallback 当成完整 LLM 结果

## 数据语义（重要）

- 采集端每 600 秒轮询一次 Steam，这是 near-real-time，不是秒级。
- `llm_refresh` 默认每 180 秒补一次新评论的 LLM 标签。
- `pending_llm_rows > 0` 表示还有新评论没补 LLM；dashboard 应显示“分析中”，不要当 0。
- `data_status=stale_source`：采集服务没跑或数据旧。
- `insufficient_window` / `insufficient_sample`：样本不足，`metric` 可能是 `null`，不要补成 0。
- 推荐曲线桶：1 小时或 10 分钟，不建议秒级。
- 实体列表以 `/v1/sentiment/entities` 为准，不要在 dashboard 里硬编码。

## 常见问题

- **8000 被占用**：确认是否已有采集服务；否则改 `steam_reviews/config.json` 的 `port`，启动时和所有 consumer 都传对应的 `--base-url`。
- **8100 被占用**：`start_api.bat` 里改 `--port`。
- **API 返回 404**：该游戏分析库不存在，先跑对应 `sentiment_analysis run`。
- **`llm_coverage < 1`**：LLM worker 还没补完，或没有密钥；用 `/pending` 看剩余行数。
- **结果和样例不一样**：样例汇总来自我们本地采集的数据；你自己采集的数据集和 stream 不同，这是正常的。
- **不要直接改采集端**：`cs2_reviews/`、`examples/`、`requirements.txt`、`setup.bat`、`start.bat` 保持原样；新代码都在 `sentiment_analysis/`。

## 当前状态

- 最新实现：LLM 主检测 + VADER fallback，`prompt_version=llm-entity-v2-overall`
- 180 条 clean holdout 合计：accuracy 0.806 / macro-F1 0.731 / kappa 0.667
- 测试：`222 passed`
- 三游戏 `verify` 均 `ok`、`llm_coverage=1.0`（以我们本地采集数据为准）
