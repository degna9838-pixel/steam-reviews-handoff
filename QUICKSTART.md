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

## 填入自己的 API key（LLM 主检测需要）

密钥**只放在 WSL 用户目录的 `~/.env_secrets`**，不要放仓库、`config.json`、Windows 环境变量、代码或聊天里。

### 方式 A：CityU 网关（推荐）

在 Windows 终端执行（把 `<你的WSL用户>` 换成 `whoami` 看到的用户；仓库在 D: 时路径是 `/mnt/d/...`）：

```powershell
wsl.exe -d Ubuntu-24.04 -u <你的WSL用户> -- bash -c "nano ~/.env_secrets"
```

写入三行（`sk-...` 换成你的 key）：

```bash
export CITYUCS_LLM_BASE_URL="https://llm.cs.cityu.edu.hk/v1"
export CITYUCS_LLM_API_KEY="sk-你的key"
export CITYUCS_LLM_MODEL="CS5351/DeepSeek-V4-Flash"
```

保存后收紧权限并测试：

```powershell
wsl.exe -d Ubuntu-24.04 -u <你的WSL用户> -- bash -c "chmod 600 ~/.env_secrets"
wsl.exe -d Ubuntu-24.04 -u <你的WSL用户> -- bash -c "cd /mnt/d/CS_SEMA/5481_data/group_project/steam-reviews-handoff/steam_reviews && bash sentiment_analysis/calibration/run_cityu_calibration.sh --probe"
```

预期输出类似：

```json
{"http_status": 200, "parsed": "neutral", "error": null}
```

### 方式 B：其他 OpenAI 兼容接口

如果你的 key 不是 CityU 网关，可在同一个 `~/.env_secrets` 里改用通用变量：

```bash
export SENTIMENT_LLM_BASE_URL="https://api.deepseek.com/v1"
export SENTIMENT_LLM_API_KEY="sk-你的key"
export SENTIMENT_LLM_MODEL="deepseek-chat"
```

`run_cityu_llm.sh` / `run_cityu_calibration.sh` 会优先用 `CITYUCS_*`，没有时自动回退到 `SENTIMENT_LLM_*`。不同模型的输出风格不同，之前 180 条 holdout 的指标不能直接套用。

### WSL 发行版/用户不同

`llm_refresh` 默认 `Ubuntu-24.04`、默认 WSL 用户。若不是这个发行版/用户：

```powershell
.\sentiment_analysis\start_llm_refresh.bat --wsl-distro Debian --wsl-user alice
```

或者设置环境变量 `SENTIMENT_WSL_DISTRO` / `SENTIMENT_WSL_USER`。

### 没有 key

跳过 `start_llm_refresh`，仍然可以跑采集 + VADER + 结果 API；API 用 `scorer=effective`，`data_status` 会是 `vader_only` 或 `llm_partial`。不要伪造 LLM 标签，也不要把 VADER fallback 当成完整 LLM 结果。

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

## 没有 LLM 密钥怎么办

完整填 key 步骤见上面“填入自己的 API key”。没有 key 时：

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
