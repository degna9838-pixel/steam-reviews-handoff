# 实体情感与显著变化检测（CS5481 Topic 4 / handoff 工作2）

> 组员从 GitHub 拉取后，先看仓库根目录的 `QUICKSTART.md`；dashboard 接口见 `docs/SENTIMENT_API.md`。

本模块是 Steam 评论采集清洗端（v1.3.0）的独立下游消费者。它只通过本机 HTTP API 读取 `/v1/reviews/changes`，不修改 `cs2_reviews/`、`examples/consume_api.py`、`requirements.txt` 或启动脚本。三款游戏（CS2=730、Dota 2=570、影之刃零=4115450）各自使用独立的分析库、stream、cursor、实体配置和结果文件。

## 快速开始

```powershell
cd steam_reviews
.\setup.bat
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pip install -r sentiment_analysis\requirements-sentiment.txt
.\.venv\Scripts\python.exe -m cs2_reviews doctor
.\.venv\Scripts\python.exe -m pytest tests sentiment_analysis/tests -q
```

终端 A 启动采集/API（样例用 `--sample`，真实模式去掉）：

```powershell
.\start.bat --sample
# 或真实模式；先只 CS2：
.\start.bat --app-id 730
# 三游戏全量：
.\start.bat
```

终端 B 检查服务和游戏状态：

```powershell
.\.venv\Scripts\python.exe -m sentiment_analysis doctor
```

样例模式（consumer 名必须含 `demo`）：

```powershell
.\.venv\Scripts\python.exe examples\consume_api.py --consumer sentiment_demo --app-id 730 --once
.\.venv\Scripts\python.exe -m sentiment_analysis run --app-id 730 --consumer sentiment_demo --once
.\.venv\Scripts\python.exe -m sentiment_analysis run --all-games --consumer sentiment_demo --once
.\.venv\Scripts\python.exe -m sentiment_analysis summarize --app-id 730 --consumer sentiment_demo --no-fetch --now 2026-11-01T01:00:00Z
```

真实模式（先少量，再全量）：

```powershell
.\.venv\Scripts\python.exe examples\consume_api.py --consumer sentiment_live --app-id 730 --once
.\.venv\Scripts\python.exe -m sentiment_analysis run --app-id 730 --consumer sentiment_algorithm_v1 --once --max-batches 5
.\.venv\Scripts\python.exe -m sentiment_analysis run --app-id 730 --consumer sentiment_algorithm_v1 --once
.\.venv\Scripts\python.exe -m sentiment_analysis summarize --app-id 730 --consumer sentiment_algorithm_v1 --window-hours 1 --baseline-hours 24
.\.venv\Scripts\python.exe -m sentiment_analysis summarize --app-id 730 --consumer sentiment_algorithm_v1 --window-hours 24 --baseline-hours 168
```

持续运行时每游戏一个进程（不要省略 `--app-id`）：

```powershell
.\.venv\Scripts\python.exe -m sentiment_analysis run --app-id 730 --consumer sentiment_algorithm_v1
.\.venv\Scripts\python.exe -m sentiment_analysis run --app-id 570 --consumer sentiment_algorithm_v1
.\.venv\Scripts\python.exe -m sentiment_analysis run --app-id 4115450 --consumer sentiment_algorithm_v1
```

`--all-games` 只支持 `--once`；持续模式请用三个进程，或改成自己的调度器。

## 处理流程

1. 读取 `/v1/health` 与 `/v1/games`，显式校验 `app_id`、`stream_id`、服务模式。
2. 从本地 `state.cursor`（首次为 0）读取 `/v1/reviews/changes?app_id=...&cursor=...&limit=100`。
3. 复用 handoff 的 `examples/consume_api.py::save_batch()`：评论镜像、分析结果和游标在同一 SQLite 事务中提交，失败整批回滚。
4. 对每条 upsert 做幂等处理：
   - 新评论/文本变化/清洗版本变化/实体配置或算法变化：删除旧分析行后重算。
   - 仅互动数（`votes_up`/`comment_count`）变化导致 revision 增加：指纹相同，复用旧分析，只更新 revision。
   - `usable_for_sentiment=false`：删除旧分析行，写 `excluded_unusable`，不把不可用当负面/中性。
   - 评论被修改、实体消失、重清洗：旧实体贡献随旧分析行一起撤销。
   - 空文本但 usable 为真：写 `skipped_empty`，不静默归中性。
5. 每次运行结束（或 `--max-batches` 到达）重新读取 health，写窗口汇总。

`save_batch()` 已校验页面 `schema_version`、`stream_id`、事件顺序、`review_id/revision` 一致性、`pipeline_version ∈ {clean-en-v1,v2,v3}` 和互动数类型；未知版本/错流/409 会停止且不推进游标。

## 实体配置

每游戏一份 JSON：`sentiment_analysis/configs/entities_<app_id>.json`。字段：

- `entity_id`、`name`
- `scope`: `document`（整条评论）或 `clause`（分句）
- `require_mention`: 默认 `clause` 为 true；整体 `game` 实体显式 false
- `aliases`: 大小写不敏感、按长度降序、自定义词边界 `(?<![A-Za-z0-9])...(?![A-Za-z0-9])`，避免 `vac` 命中 `vacation`

分句规则：句子标点/换行/分号 + 转折词（but/however/although/though/while/whereas/yet/still）+ 逗号；过短的无情感片段与相邻片段合并。方面实体只统计包含其别名的分句；整体 `game` 实体对每条 usable 评论都计分（`mention_required=false`），因为评论本来就属于该 app_id 的游戏。

当前实体：

| 游戏 | 实体 |
|---|---|
| CS2 | game, anticheat, matchmaking, servers, skins_economy, performance, weapon_balance, monetization |
| Dota 2 | game, matchmaking, hero_balance, patch_meta, monetization, servers_performance, report_system, new_player_experience |
| Phantom Blade Zero | game, combat, story, graphics_art, performance_pc, boss_design, difficulty, price_value |

改配置或算法版本后，已有评论不会被 changes 流重放；必须先 `rebuild --app-id <id>`（从本地 `reviews` 镜像全量重算，不改游标），否则 `run` 会拒绝继续。

## 情感算法

**当前主检测：CityU LLM（`CS5351/DeepSeek-V4-Flash`）**。对每条 review × VADER 识别出的实体（含整体 game），把完整 `text_clean` 和实体名发给 LLM，要求返回 `positive/negative/neutral + reason`。LLM 标签写入 `llm_analysis` 表；`effective_analysis` 视图优先使用 LLM，未覆盖的实体回退到 VADER。这样既利用 LLM 对反讽/上下文的能力，又保留可复现的本地 fallback。

VADER fallback：`vaderSentiment==3.3.2` + `domain_lexicon.json`；compound 阈值 ±0.05；分句评分；只作 fallback 和 baseline。输入只用 `text_clean`，不使用 `tokens_trend`，不把 `steam_recommended` 当情感真值。

LLM prompt 要点：实体名 + 完整评论；明确 positive/negative/neutral 规则；反讽按真实意图；请求修复视为 negative；正负平衡视为 neutral；严格 JSON，`reason` 简短。批量模式把最多 8 条 review-entity 放进一次请求，减少调用次数；解析失败会回退为单条调用。

局限：LLM 也有错误，且 `neutral` 仍最难（人工 120 条上 neutral F1 约 0.39）；LLM 不是人工真值；同一 review 的实体由 VADER 别名匹配决定，别名漏召回仍会漏实体；Steam 英文筛选和采样偏差仍在。

## LLM 主检测接入流程

```powershell
# 1) 从本地分析库导出待标注的 review × entity jobs（默认跳过已有 LLM 标签）
.\.venv\Scripts\python.exe -m sentiment_analysis export-llm-jobs `
  --app-id 730 --consumer sentiment_algorithm_v1 `
  --out analysis\games\730\sentiment\sentiment_algorithm_v1\llm\jobs.jsonl

# 2) 在 WSL 内调用 CityU 网关（批大小 8、4 并发、断点续跑）
#    只从 ~/.env_secrets 读取 CITYUCS_LLM_*，不写密钥
wsl.exe -d Ubuntu-24.04 -u otojf7 -- bash -lc "cd /mnt/d/CS_SEMA/5481_data/group_project/steam-reviews-handoff/steam_reviews && bash sentiment_analysis/calibration/run_cityu_llm.sh --input analysis/games/730/sentiment/sentiment_algorithm_v1/llm/jobs.jsonl --output analysis/games/730/sentiment/sentiment_algorithm_v1/llm/predictions.jsonl --workers 4 --batch-size 8 --max-batch-chars 3000 --sleep 0.1 --resume"

# 3) 导入 LLM 标签；effective_analysis 立即优先 LLM
.\.venv\Scripts\python.exe -m sentiment_analysis import-llm-labels `
  --app-id 730 --consumer sentiment_algorithm_v1 `
  --predictions analysis\games\730\sentiment\sentiment_algorithm_v1\llm\predictions.jsonl

# 4) 重算汇总；llm_coverage=1.0 时 data_status=ok，否则 llm_partial
.\.venv\Scripts\python.exe -m sentiment_analysis summarize --app-id 730 --consumer sentiment_algorithm_v1 --window-hours 24 --baseline-hours 168
```

成本/时间参考：CS2 约 1.93 万个 review×entity jobs；批大小 8、4 并发时约 40–60 分钟（受网关限流影响）。三游戏合计约 4.6 万个 jobs，建议按游戏分批跑，每批完成后导入并检查 `verify`。429 会自动重试；失败项用 `--resume` 补跑。

## 实时结果 API（dashboard 组员入口）

本模块提供一个只读 API，给后续做情绪曲线的组员轮询；不需要他们读 SQLite 或采集库。完整契约见 [`docs/SENTIMENT_API.md`](docs/SENTIMENT_API.md)。

启动：

```powershell
# 1) 采集服务（handoff 提供，8000）
.\start.bat

# 2) 实时消费 changes（每游戏一个进程；持续运行）
.\.venv\Scripts\python.exe -m sentiment_analysis run --app-id 730 --consumer sentiment_algorithm_v1
.\.venv\Scripts\python.exe -m sentiment_analysis run --app-id 570 --consumer sentiment_algorithm_v1
.\.venv\Scripts\python.exe -m sentiment_analysis run --app-id 4115450 --consumer sentiment_algorithm_v1

# 3) LLM 增量补标（每 180 秒；--once 可测试一次）
.\.venv\Scripts\python.exe -m sentiment_analysis.llm_refresh --interval 180

# 4) 结果 API（8100）
.\.venv\Scripts\python.exe -m sentiment_analysis serve --port 8100
```

dashboard 主要用：

```http
GET http://127.0.0.1:8100/v1/sentiment/games
GET http://127.0.0.1:8100/v1/sentiment/entities?app_id=730
GET http://127.0.0.1:8100/v1/sentiment/series?app_id=730&entity_id=game&days=7&bucket_hours=1&scorer=llm
GET http://127.0.0.1:8100/v1/sentiment/alerts?app_id=730&window_hours=24&baseline_hours=168&scorer=llm
```

关键约定：

- `scorer=llm` 默认只返回 LLM 标签，不把 VADER fallback 画进曲线；`scorer=effective` 才混用 fallback。
- `pending_llm_rows > 0` 表示新评论还没补 LLM；dashboard 应显示“分析中”或暂时保留上一点。
- 采集端是 600s 轮询，建议曲线用 1 小时或 10 分钟桶，不要做秒级。
- `llm_coverage`、`data_status`、`processed_cursor`、`source_latest_cursor`、`cursor_lag` 每页都返回；`insufficient_window`/`insufficient_sample` 不能当 0。

## 结果契约

分析库（每游戏 × 每 consumer 独立）：

```text
analysis/games/<app_id>/sentiment/<consumer>/sentiment.sqlite3
analysis/games/<app_id>/sentiment/<consumer>/summaries/<W>h_<B>h/latest.json
analysis/games/<app_id>/sentiment/<consumer>/summaries/<W>h_<B>h/summary_<UTC>.json
analysis/games/<app_id>/sentiment/<consumer>/summaries/dashboard.json
analysis/games/<app_id>/sentiment/<consumer>/calibration/calibration_report.json
```

默认窗口 `1h_24h`；报告也可用 `24h_168h`。汇总关键字段：

```json
{
  "schema_version": "sentiment-summary-1.0",
  "app_id": 730,
  "game": "CS2",
  "consumer": "sentiment_algorithm_v1",
  "mode": "live",
  "source_mode": "live",
  "dataset_type": null,
  "source_stream_id": "...",
  "processed_cursor": "18446",
  "source_latest_cursor": "18446",
  "cursor_lag": 0,
  "backfill_status": "completed",
  "provisional": false,
  "data_status": "ok",
  "llm_coverage": 1.0,
  "scorer_counts": {"llm": 19294, "vader": 0},
  "window": {"current_start": "...", "current_end": "...", "baseline_start": "...", "baseline_end": "..."},
  "algorithm": {"name": "llm-entity", "version": "sentiment-llm-v1", "llm_model": "CS5351/DeepSeek-V4-Flash", "vader_fallback_version": "sentiment-v1", "min_mentions": 5, "change_threshold": 0.15, "alpha": 0.05},
  "entities": [{
    "entity_id": "anticheat",
    "current": {"positive_count": 7, "negative_count": 46, "neutral_count": 4, "mention_count": 57, "metric": -0.684},
    "baseline": {"positive_count": 0, "negative_count": 0, "neutral_count": 0, "mention_count": 0, "metric": null},
    "change": null,
    "change_flag": false,
    "alert_type": "none",
    "significance": {"test": "chi_square_independence", "df": 2, "p_value": null, "conditions_met": false, "significant": false},
    "data_status": "insufficient_baseline",
    "provisional": true
  }]
}
```

- `metric` = `(positive - negative) / mention_count`，范围 -1..1；`mention_count=0` 时为 `null`，不置零。
- `change` = 当前 metric − 基线 metric；只有两窗口 `mention_count >= min_mentions` 且 `|change| >= change_threshold` 才 `change_flag=true`。
- 额外做 3×2 卡方独立性检验（标签分布 × 当前/基线，df=2，`p=exp(-chi2/2)`）；所有非零期望频数 >=5 才输出 `p_value`，`p<alpha` 才 `alert_type="statistical"`。未做多实体/多窗口全局多重比较校正，报告中按探索性结果解释。
- `data_status`: `ok / insufficient_window / insufficient_baseline / backfill_incomplete / stale_source / mixed_pipeline_versions / llm_partial / vader_only / sample / no_data`。`llm_coverage` 是 LLM 已标注的 review×entity 行数 / VADER 分析行数；`<1` 时 `provisional=true`。`scorer_counts` 显示实际使用的 LLM/VADER 行数。
- `analysis` 表逐评论 × 实体保存 `review_id, revision, entity_id, sentiment_label, sentiment_score, evidence, created_at, pipeline_version, algorithm_version, entity_config_version, analyzed_at, reused_from_revision`；`export-reviews` 导出 JSONL。
- `export-dashboard` 输出按小时对齐的时间序列（`bucket_start`, `bucket_end`, 三标签计数, `mention_count`, `net_metric`, `data_status`），不生成图表。
- 删除的 Steam 评论在 collector 的 changes 流中没有 tombstone，无法检测；修改/重清洗会回溯影响历史窗口；汇总只代表 `processed_cursor` 时点的数据。

## 验收与检查命令

```powershell
.\.venv\Scripts\python.exe -m sentiment_analysis verify --app-id 730 --consumer sentiment_algorithm_v1
.\.venv\Scripts\python.exe -m sentiment_analysis export-reviews --app-id 730 --consumer sentiment_algorithm_v1 --out analysis\games\730\sentiment\sentiment_algorithm_v1\reviews.jsonl
.\.venv\Scripts\python.exe -m sentiment_analysis export-dashboard --app-id 730 --consumer sentiment_algorithm_v1 --days 7 --bucket-hours 1 --out analysis\games\730\sentiment\sentiment_algorithm_v1\summaries\dashboard.json
```

`verify` 检查：reviews 与 review_status 计数一致、无孤儿分析行、excluded/skipped 无分析行、analysis.revision 与 review_status.revision 一致、无其他算法版本、app_id/stream/cursor 绑定。任何问题返回非零。

## 如何验证情感分析效果

先分清两个层面：

1. **标签层面**：单条评论 × 实体判成 positive/negative/neutral，是否与人工/独立判断一致。
2. **变化层面**：窗口 metric 的变化是否真实，是否被样本不足、回填未完成、多重比较或数据偏差误导。

### 1. 人工金标准集（最推荐，报告里最有说服力）

从真实数据分层抽样，做**盲标**（标注时不显示 VADER 标签）：

```powershell
# 1) 分层抽样（每游戏可各做 60-120 条）
.\.venv\Scripts\python.exe -m sentiment_analysis export-samples --app-id 730 --consumer sentiment_algorithm_v1 --limit 120 --out analysis\games\730\sentiment\sentiment_algorithm_v1\calibration\samples.jsonl

# 2) 生成盲标表：不含 vader_label/vader_score，只留 sample_id/entity/text/human_label
.\.venv\Scripts\python.exe sentiment_analysis\calibration\make_annotation_sheet.py --input analysis\games\730\sentiment\sentiment_algorithm_v1\calibration\samples.jsonl --out analysis\games\730\sentiment\sentiment_algorithm_v1\calibration\annotation_sheet.jsonl

# 3) 人工填写 human_label（positive/negative/neutral）和 annotator；
#    有两个人时，各自独立标一份，先算标注者间 kappa，再算与 VADER 的一致率。

# 4) 合并回 VADER 样本（默认只输出已标注行）
.\.venv\Scripts\python.exe sentiment_analysis\calibration\merge_annotation.py --samples analysis\games\730\sentiment\sentiment_algorithm_v1\calibration\samples.jsonl --labels analysis\games\730\sentiment\sentiment_algorithm_v1\calibration\annotation_sheet.jsonl --out analysis\games\730\sentiment\sentiment_algorithm_v1\calibration\labeled_samples.jsonl

# 5) 评估：accuracy / macro-F1 / 每类 P-R-F1 / 3×3 混淆矩阵 / Cohen's kappa / 分实体
.\.venv\Scripts\python.exe -m sentiment_analysis evaluate --input analysis\games\730\sentiment\sentiment_algorithm_v1\calibration\labeled_samples.jsonl --gold-field human_label --pred-field vader_label --out analysis\games\730\sentiment\sentiment_algorithm_v1\calibration\evaluation.json
```

报告至少写：抽样方法、样本量、标注人、标注者间 kappa（若有两人）、accuracy、macro-F1、混淆矩阵、分实体结果和典型错误类型。只有 30–50 条也能看出趋势，但置信区间会很宽，不要把它当成全量准确率。

### 2. LLM 抽样校准（已完成，作为弱监督参考）

已对三游戏各抽样比较 VADER 与 CityU `CS5351/DeepSeek-V4-Flash`：CS2 120 条一致率 0.508/kappa 0.262，Dota 2 40 条 0.525/0.280，Phantom 40 条 0.450/0.181。它说明“词典结果与独立 LLM 判断的中等一致”，不是准确率，也不是人工真值；`neutral` 边界和实体作用域是主要分歧来源。命令见下一节。

### 3. 构造用例与回归测试

```powershell
.\.venv\Scripts\python.exe -m pytest tests sentiment_analysis/tests -q
```

当前 211 passed，覆盖否定（`not good`）、转折（`fun but VAC bad`）、多实体相反态度、短评（`gg`）、不可用/空文本、修改/实体消失、重复批次、错流/未知版本、跨游戏同 review_id 隔离、窗口边界、卡方条件、标注/合并/评估指标。VADER 是确定性规则，没有训练集/测试集划分；这些测试验证的是规则和管道，不是统计准确率。

### 4. 变化检测效果

- **已知事件对照**：选一个有明确事件的窗口（如反作弊更新、版本平衡补丁、差评潮），看对应实体 metric 是否同向变化；再看 `evidence` 里的评论是否真的在谈该实体。
- **窗口稳定性**：用不同 `--now`（如每小时滚动）重算 1h/24h 和 24h/7d，结论方向是否稳定；1h 稀疏时只报 `insufficient_*`，不硬下结论。
- **误报检查**：对 `change_flag=true` 的实体，抽 10–20 条 `evidence` 人工看是否被整体好评/差评带偏；多实体同句是已知限制。
- **回填/样本状态**：确认 `backfill_status=completed`、`data_status=ok`、`provisional=false`，且 `mention_count >= min_mentions`；否则只能叫 provisional 告警。
- **统计条件**：卡方只在不同标签的期望频数都 >=5 时给 `p_value`；不显著不等于没变化，显著也不等于因果。

### 5. 与 `steam_recommended` 的对照（只作描述，不作真值）

可以统计“文本情感 vs 作者是否推荐”的表面一致率，用来说明两者不同；但 handoff 明确禁止把 `steam_recommended` 当情感真值或直接映射标签，所以不要用它算 accuracy/F1。

### 6. 本次人工盲标结果（CS2，120 条）

已由用户完成 120 条 entity-review 盲标（negative 69 / positive 31 / neutral 20），同一批样本比较 VADER 与 CityU LLM：

| 方法 | accuracy | macro-F1 | Cohen's kappa |
|---|---:|---:|---:|
| VADER + domain lexicon | 0.492 | 0.475 | 0.237 |
| LLM 旧单实体校准 prompt | 0.700 | 0.621 | 0.474 |
| LLM 单条主检测 prompt | 0.750 | 0.657 | 0.549 |
| LLM 生产配置（批 24、每条截断 600 字符） | **0.808** | **0.734** | **0.657** |

VADER 混淆矩阵（行=人工，列=VADER）：

```text
human\vader   positive negative neutral
positive            18        4       9
negative            19       30      20
neutral              3        6      11
```

LLM 生产配置混淆矩阵（行=人工，列=LLM）：

```text
human\llm     positive negative neutral
positive            25        2       4
negative             2       63       4
neutral              2        9       9
```

解读：
- VADER 的主要问题是**漏判负面**：69 条人工 negative 中只有 30 条被判 negative，19 条被判 positive、20 条被判 neutral；正面 precision 只有 0.45，neutral 的 F1 只有 0.37。
- LLM 生产配置明显更好：negative recall 0.913、positive precision 0.862、negative F1 0.881；neutral 仍然难（F1 0.486），9 条 neutral 被判 negative。
- 分实体：VADER 在 `weapon_balance`（0.667）、`skins_economy`（0.600）较好，`anticheat` 最差（0.267，kappa 为负）；LLM 在 `game`、`servers`、`skins_economy`、`anticheat` 较好；`anticheat` 只有 1 条 neutral，kappa 受类别不平衡影响，不能只看 kappa。
- 样本是按旧 VADER 标签分层的“压力测试集”（每个 entity×label 5 条），不是随机样本；随机线上样本的 accuracy 可能不同。单标注人、无标注者间 kappa，结论应写成“在这 120 条盲标样本上”。
- 结果文件：`calibration/evaluation_vader.json`、`calibration/evaluation_llm_primary.json`、`calibration/evaluation_llm_batched.json`。原始盲标 CSV 与逐条文本留在本机（已被 `.gitignore` 排除），仓库只提交聚合指标。

如果目标是报告里更强的情感准确率，**LLM 全量或 LLM 复核 top-change 实体**比继续调 VADER 词典更有效；如果坚持本地无密钥词典方案，应把 VADER 定位为可复现 baseline，并如实报告上述限制。

### 7. 预处理 / Prompt 优化实验（同一 120 条）

在已经标注的 120 条上试了四种输入/提示，结论是**不要裁剪上下文**：

| 版本 | accuracy | macro-F1 | kappa | 说明 |
|---|---:|---:|---:|---|
| 当前生产（全文截断 600、simple prompt） | 0.808 | 0.734 | 0.657 | 现有 `llm_analysis` 标签 |
| 实体窗口上下文（±1 句，900 字符）+ 别名/证据/few-shot | 0.733 | 0.674 | 0.540 | 裁掉整体语境后 neutral 增多 |
| 全文 600 + 别名/证据/few-shot | 0.725 | 0.667 | 0.535 | 额外指令反而降低正类 recall |
| 全文 600 + simple + “局部不清时看整体态度” | **0.825** | **0.764** | **0.690** | 唯一稳定增益；neutral F1 0.556 |

原因：这 120 条人工标签里很多是**按整条评论的总体态度**判的（例如整体差评里的 monetization、整体好评里的 matchmaking）。实体窗口只留下局部句子，会把“整体差评/好评”信号删掉，LLM 就偏向 neutral。别名、证据引用、few-shot 在这个样本上没有带来增益，可能还会让模型过度谨慎。

可复用命令（实验版，不覆盖生产标签）：

```powershell
# 先在 120 条人工样本上跑 simple_overall；生产默认仍是 simple
bash sentiment_analysis/calibration/run_cityu_llm.sh `
  --input work/llm_val_jobs.jsonl --output work/llm_val_overall_pred.jsonl `
  --workers 3 --batch-size 24 --max-batch-chars 30000 --max-text-chars 600 `
  --prompt-style simple_overall --sleep 0.05
```

### 8. 二次复核 / 投票与 token 性价比（同一 120 条）

batch 输出现在记录每个请求的 `usage`（按 batch size 均摊到每条），可直接估 token。实验：

| 方案 | accuracy | macro-F1 | kappa | 120 条总 token | 额外 token |
|---|---:|---:|---:|---:|---:|
| baseline `simple_overall` | 0.825 | 0.772 | 0.690 | 12,380 | — |
| neutral 二次复核（只对 16 条 neutral） | 0.800 | 0.724 | 0.639 | 13,828 | +1,448 / +11.7% |
| `simple` prompt | 0.833 | 0.775 | 0.704 | 12,324 | 持平 |
| 两 prompt 投票 + judge | 0.833 | 0.772 | 0.703 | 26,371 | +13,991 / +113% |

结论：

- 单条约 103 token；即使 2 万条也只有约 2M token，**token 不是瓶颈，网关调用次数/时间才是**。
- neutral 二次复核把 neutral F1 从 0.611 降到 0.485，反而变差；不采用。
- 投票的 0.833 与单次 `simple` 相同，提升来自 prompt/随机波动，不是投票；多花一倍 token 不划算。
- `simple` 在 120 条上略高，但 clean holdout 只有 0.667；再次说明不能用 120 条调参集选模型。
- 结论：保持单次 `simple_overall`，不做二次复核/投票。结果文件 `calibration/experiment_secondary_passes.json`。

注意：120 条已经用于挑 prompt，0.825 偏乐观。随后又做了 **100 条不重叠的 clean holdout**（随机按实体抽样，不是按 VADER 标签分层）：

| 方法 | accuracy | macro-F1 | kappa | 95% accuracy CI |
|---|---:|---:|---:|---|
| 当前 `simple` | 0.680 | 0.598 | 0.441 | 0.59–0.77 |
| `simple_overall` | **0.830** | **0.747** | **0.683** | 0.75–0.90 |

clean holdout 上 `simple_overall` 的 positive recall 0.741（simple 0.519）、negative recall 0.918（simple 0.770）、neutral F1 0.538（simple 0.389）。这确认了提升是真实的，全量已重标为 `prompt_version=llm-entity-v2-overall`。

三游戏独立 holdout 都支持该结论：

| 样本 | 方法 | accuracy | macro-F1 | kappa |
|---|---|---:|---:|---:|
| CS2 100 | simple | 0.680 | 0.598 | 0.441 |
| CS2 100 | **simple_overall** | **0.830** | **0.747** | **0.683** |
| Dota 2 40 | simple | 0.725 | 0.621 | 0.457 |
| Dota 2 40 | **simple_overall** | **0.825** | **0.746** | **0.642** |
| Phantom 40 | simple | 0.575 | 0.444 | 0.211 |
| Phantom 40 | **simple_overall** | **0.725** | **0.606** | **0.463** |
| **三游戏合计 180** | simple | 0.667 | 0.596 | 0.459 |
| **三游戏合计 180** | **simple_overall** | **0.806** | **0.731** | **0.667** |

Dota/Phantom 每游戏只有 40 条，单实体 n=5，分实体指标噪声大；合并 180 条的结果更适合作为跨游戏泛化结论。`llm-entity-v1` 的旧预测仍保留在 `llm/predictions.jsonl`，需要回滚时可重新导入。

## LLM 抽样校准（可选，CityU 网关）

1. 从分析库分层导出样本（不含平台推荐选择作为真值）：

```powershell
.\.venv\Scripts\python.exe -m sentiment_analysis export-samples --app-id 730 --consumer sentiment_algorithm_v1 --limit 120 --out analysis\games\730\sentiment\sentiment_algorithm_v1\calibration\samples.jsonl
```

2. 在 WSL 内运行（从 `~/.env_secrets` 读 `CITYUCS_LLM_*`，或回退到 `SENTIMENT_LLM_*`；填 key 的步骤见仓库根目录 `QUICKSTART.md`，不要把密钥写入项目）：

```bash
cd /mnt/d/CS_SEMA/5481_data/group_project/steam-reviews-handoff/steam_reviews
bash sentiment_analysis/calibration/run_cityu_calibration.sh \
  --input analysis/games/730/sentiment/sentiment_algorithm_v1/calibration/samples.jsonl \
  --output analysis/games/730/sentiment/sentiment_algorithm_v1/calibration/llm_predictions.jsonl \
  --limit 120 --sleep 0.2
```

3. 比较：

```bash
python3 sentiment_analysis/calibration/compare_calibration.py \
  --input analysis/games/730/sentiment/sentiment_algorithm_v1/calibration/samples.jsonl \
  --predictions analysis/games/730/sentiment/sentiment_algorithm_v1/calibration/llm_predictions.jsonl \
  --out analysis/games/730/sentiment/sentiment_algorithm_v1/calibration/calibration_report.json
```

校准脚本的提示词不包含 VADER 标签/分数，也不包含 `steam_recommended`；输出 JSON 中保留原始响应、解析结果、HTTP 状态和错误类型。密钥只从环境读取，脚本不 echo 密钥；原始预测 JSONL 被 `.gitignore` 排除，只提交聚合报告。

## 当前三游戏结果（截至 2026-11-09）

三游戏均已完成 30 天回填，LLM 主标签导入完成，`llm_coverage=1.0`、`data_status=ok`、`provisional=false`，`verify` 均通过。24h/7d 结果（LLM 主检测）：

| 游戏 | 总评论 | 已分析 | LLM 标注行 | 当前提及 | 当前 metric | 基线提及 | 基线 metric | 变化 | 告警 | 状态 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| CS2 | 18,748 | 15,699 | 19,601 | 1,095 | 0.740 | 3,009 | 0.295 | +0.445 | statistical | ok |
| Dota 2 | 2,873 | 2,163 | 2,589 | 47 | 0.021 | 450 | 0.082 | -0.061 | none | ok |
| Phantom Blade Zero | 10,927 | 9,824 | 22,154 | 504 | 0.802 | 4,755 | 0.810 | -0.008 | none | ok |

分实体告警（24h/7d，`prompt_version=llm-entity-v2-overall`）：

- CS2：整体 metric 从 0.295 升到 0.740（+0.445，卡方显著）；`anticheat` 当前 48 次、-0.917（基线 465 次、-0.923）；`matchmaking` 当前 9 次、-0.889（基线 111 次、-0.730，阈值告警）；`skins_economy` 当前 5 次、0.000（基线 64 次、-0.281，阈值告警）；`performance` 当前 31 次、-0.032（基线 54 次、-0.204，卡方显著）；`weapon_balance` 当前 9 次、+0.111（基线 48 次、-0.292，阈值告警）；`servers`/`monetization` 当前 <5 次，`insufficient_window`。
- Dota 2：整体稳定；`matchmaking` 当前 4 次、-1.000（基线 36 次、-0.833）；其他实体当前样本 0–2 条，`insufficient_window`。
- Phantom Blade Zero：整体稳定；`performance_pc` 当前 61 次、+0.115（基线 431 次、-0.084，变化 +0.198，阈值告警）；`boss_design` 当前 129 次、+0.442（基线 898 次、+0.563，卡方显著但变化未过阈值）；`combat` +0.718；`story` +0.328；`graphics_art` +0.755；`difficulty` +0.283；`price_value` +0.558。
- 1h/24h 窗口仍稀疏（CS2 约 81 条、Dota 2 极少、Phantom 约 9 条），只用于演示实时告警链路；结论以 24h/7d 为准。

首次少量验证：CS2 cursor 0→500（5 页）后汇总，`data_status=backfill_incomplete`、`provisional=true`，用于验证“先少量再全量”流程，不作为最终结果。之后完成 30 天回填和 LLM 全量标注，再重算 1h/24h 与 24h/7d。

人工盲标验证（CS2，120 条 entity-review；VADER vs LLM 生产配置）：

| 方法 | accuracy | macro-F1 | Cohen's kappa |
|---|---:|---:|---:|
| VADER + domain lexicon | 0.492 | 0.475 | 0.237 |
| LLM 生产配置（批 24、截断 600 字符） | **0.808** | **0.734** | **0.657** |

该结果说明 LLM 主检测明显优于词典 baseline，尤其在 negative recall（0.913）和整体一致率上；neutral 仍是最难类别（F1 0.486）。样本是按旧 VADER 标签分层的压力测试集、单标注人，结论仅限这 120 条盲标样本。

## 文件

```text
sentiment_analysis/
├─ settings.py, entities.py, sentiment.py, store.py, aggregate.py, client.py, cli.py
├─ llm.py                       # LLM job 导出、预测导入、effective_analysis 优先级
├─ llm_refresh.py               # 持续增量补 LLM 标签的 worker
├─ api.py                       # 只读结果 API（给 dashboard，默认 LLM-only）
├─ evaluate.py                  # 人工金标准评估
├─ docs/SENTIMENT_API.md        # dashboard 组员接口契约
├─ configs/entities_730.json, entities_570.json, entities_4115450.json
├─ domain_lexicon.json
├─ requirements-sentiment.txt
├─ calibration/llm_label_batch.py, run_cityu_llm.sh
├─ calibration/llm_calibrate.py, compare_calibration.py, run_cityu_calibration.sh
├─ calibration/make_annotation_sheet.py, merge_annotation.py
└─ tests/
```

## 报告写法建议

- 方法：实体 + 别名 + 分句 + VADER/域词典 + 阈值 + 窗口/阈值/卡方；强调 lexicon-based 符合 Topic 4 第 4 点。
- 实验：三游戏分别报告回填状态、窗口计数、metric/change、阈值/统计告警；不要把样例当真实结果，不要把 `provisional` 结果写成最终结论。
- 校准：报告 120 条 CS2 抽样的一致率/kappa/混淆矩阵，说明这是抽样一致性，不是人工标注；讨论 neutral 边界和 entity-scope 差异。
- 讨论：反讽/混语/同句多实体/短评/词典覆盖/Steam 选择偏差/删除无 tombstone/多重比较未校正。
- 复现：Python 3.12、vaderSentiment 3.3.2、服务端口 8000、`sentiment_algorithm_v1`、窗口参数和 cursor。
```
