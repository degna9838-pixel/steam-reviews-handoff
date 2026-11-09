# 实体情感与显著变化检测（CS5481 Topic 4 / handoff 工作2）

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

- 全量：VADER `vaderSentiment==3.3.2` + 可审查的 `domain_lexicon.json`（gaming 俚语、性能/作弊/付费等）。
- 标签：`compound >= 0.05` positive，`<= -0.05` negative，否则 neutral；分数原样保存，不伪造概率/置信度。
- 分句内评分；同一实体跨多个分句取均值；证据保留命中分句。
- 输入只用 `text_clean`，不使用 `tokens_trend`，不把 `steam_recommended` 当情感真值。
- 对 `pay to win`/`pay-to-win` 归一为 `p2w`，弯引号归一为 ASCII 单引号，保留否定。

局限：反讽、引用、短文本、混语、同一分句内的多实体相反态度、游戏术语上下文歧义、词典领域覆盖不足；Steam 英文筛选和近实时采样也有选择偏差。VADER 的 compound 不是概率。LLM 校准是抽样一致性检查，不是人工真值。

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
  "backfill_status": "running",
  "provisional": true,
  "data_status": "backfill_incomplete",
  "window": {"current_start": "...", "current_end": "...", "baseline_start": "...", "baseline_end": "..."},
  "algorithm": {"name": "vader-domain", "version": "sentiment-v1", "min_mentions": 5, "change_threshold": 0.15, "alpha": 0.05},
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
- `data_status`: `ok / insufficient_window / insufficient_baseline / backfill_incomplete / stale_source / mixed_pipeline_versions / sample / no_data`。`provisional=true` 表示回填未完成/样例/陈旧数据，不能当完整窗口结论。
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

## LLM 抽样校准（可选，CityU 网关）

1. 从分析库分层导出样本（不含平台推荐选择作为真值）：

```powershell
.\.venv\Scripts\python.exe -m sentiment_analysis export-samples --app-id 730 --consumer sentiment_algorithm_v1 --limit 120 --out analysis\games\730\sentiment\sentiment_algorithm_v1\calibration\samples.jsonl
```

2. 在 WSL 内运行（只从 `~/.env_secrets` 读 `CITYUCS_LLM_*`，不把密钥写入项目）：

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

三游戏均已完成 30 天回填，`backfill_status=completed`、`data_status=ok`、`provisional=false`，`verify` 均通过。24h/7d 结果：

| 游戏 | 总评论 | 已分析 | 排除不可用 | 当前提及 | 当前 metric | 基线提及 | 基线 metric | 变化 | 告警 | 状态 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| CS2 | 18,446 | 15,411 | 3,035 | 1,055 | 0.608 | 2,834 | 0.290 | +0.318 | statistical | ok |
| Dota 2 | 2,860 | 2,154 | 706 | 64 | 0.281 | 447 | 0.268 | +0.013 | none | ok |
| Phantom Blade Zero | 10,834 | 9,744 | 1,090 | 551 | 0.530 | 4,801 | 0.565 | -0.035 | none | ok |

分实体告警（24h/7d）：

- CS2：`anticheat` 当前 57 次、metric -0.684（基线 464 次、-0.582）；`matchmaking` 当前 13 次、-0.154（基线 110 次、-0.364，阈值告警）；`skins_economy` 当前 8 次、+0.500（基线 63 次、+0.063，阈值告警）；`performance` 当前 19 次、+0.579（基线 56 次、+0.304，阈值告警）；`servers` 当前仅 4 次，`insufficient_window`。
- Dota 2：整体稳定；`matchmaking` 当前 7 次、-0.714（基线 34 次、-0.235，阈值告警）；其他实体当前样本 0–4 条，`insufficient_window`。
- Phantom Blade Zero：整体稳定；`difficulty` 当前 106 次、-0.104（基线 787 次、+0.116，变化 -0.219，卡方显著）；`combat` 当前 220 次、-0.055（基线 1,732 次、-0.047）；`boss_design` 当前 136 次、-0.243（基线 911 次、-0.236）；`story`/`graphics_art`/`price_value` 为正。
- 1h/24h 窗口普遍稀疏（CS2 当前 7 条、Dota 2 当前 3 条、Phantom 当前 12 条），只用于演示实时告警链路；结论应以 24h/7d 为准，或等更多小时数据后重算。

首次少量验证：CS2 cursor 0→500（5 页）后汇总，`data_status=backfill_incomplete`、`provisional=true`，用于验证“先少量再全量”流程，不作为最终结果。回填完成后 1h/24h 与 24h/7d 均重新计算。

LLM 抽样校准（VADER vs CityU `CS5351/DeepSeek-V4-Flash`，0 API/解析错误）：

| 游戏 | 样本 | 一致率 | Cohen's kappa | 备注 |
|---|---:|---:|---:|---|
| CS2 | 120 | 0.508 | 0.262 | neutral 有 25 条被 LLM 判 negative |
| Dota 2 | 40 | 0.525 | 0.280 | negative 一致率较高 |
| Phantom Blade Zero | 40 | 0.450 | 0.181 | negative→positive 9 条，实体局部负面 vs 整体正面差异明显 |

校准只说明“词典全量结果与 LLM 抽样判断的一致程度”，不是准确率或人工真值；VADER 的 neutral 边界、分句作用域和领域词典覆盖是主要差异来源。

## 文件

```text
sentiment_analysis/
├─ settings.py, entities.py, sentiment.py, store.py, aggregate.py, client.py, cli.py
├─ configs/entities_730.json, entities_570.json, entities_4115450.json
├─ domain_lexicon.json
├─ requirements-sentiment.txt
├─ calibration/llm_calibrate.py, compare_calibration.py, run_cityu_calibration.sh
└─ tests/
```

## 报告写法建议

- 方法：实体 + 别名 + 分句 + VADER/域词典 + 阈值 + 窗口/阈值/卡方；强调 lexicon-based 符合 Topic 4 第 4 点。
- 实验：三游戏分别报告回填状态、窗口计数、metric/change、阈值/统计告警；不要把样例当真实结果，不要把 `provisional` 结果写成最终结论。
- 校准：报告 120 条 CS2 抽样的一致率/kappa/混淆矩阵，说明这是抽样一致性，不是人工标注；讨论 neutral 边界和 entity-scope 差异。
- 讨论：反讽/混语/同句多实体/短评/词典覆盖/Steam 选择偏差/删除无 tombstone/多重比较未校正。
- 复现：Python 3.12、vaderSentiment 3.3.2、服务端口 8000、`sentiment_algorithm_v1`、窗口参数和 cursor。
```
