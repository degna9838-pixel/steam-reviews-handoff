# Person 2：趋势检测与结果导出

Person 1 提供每个游戏、每个时间窗口的 TF、DF 和有效评论数。这里把「最近 1 小时」和「再往前的 24 小时」比成趋势强度，并写成看板可以直接读的 JSON。采集、清洗和词频统计都没有改。

## 怎么算

比较的是文档频率比例，不是原始次数。一个词的比例 = 提到它的评论数 / 这段窗口里的有效评论数。1 小时和 24 小时的比例可以直接比。

```text
strength = min(20, (最近比例 + 0.05) / (基线比例 + 0.05))
```

默认参数记录在每份结果的 `parameters` 里：

| 参数 | 默认 | 含义 |
| --- | ---: | --- |
| recent_hours | 1 | 最近窗口 |
| baseline_hours | 24 | 基线窗口，紧挨在最近窗口前面，两段不重叠 |
| smoothing | 0.05 | 加在两个比例上，基线为 0 时强度仍然有限 |
| min_recent_df | 3 | 最近窗口里至少这么多条评论提到该词 |
| min_recent_reviews | 20 | 最近窗口至少这么多条有效评论，否则不算完整结论 |
| min_baseline_reviews | 30 | 基线窗口至少这么多条有效评论 |
| strength_threshold | 2.0 | 强度达到这个值才进入趋势名单 |
| max_strength | 20 | 强度上限 |

`recent_count` / `baseline_count` 是文档频率：同一条评论里同一个词只算一次。`recent_tf` / `baseline_tf` 保留重复次数，不参加打分。

基线里没出现过、最近出现了，标 `is_new=true`。只有 1 条评论提到的词，比例可以很高，但达不到最少 3 条，不会标成趋势。

`is_trending=true` 还要求这份数据允许下结论：本地快照样本够，而且上游状态是已同步的真实采集。样例模式、回填没完成、某一侧评论太少，都只给分数，不把词放进趋势名单。

## 运行

在 `steam_reviews` 目录：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_trend_*.py" -v
.\.venv\Scripts\python.exe -m trend_analysis.run --experiment
```

实验读的是构造出来的评论，用来核对公式。结果在 `analysis/experiments/fixture_report.json`。这不是 Steam 上的结论。

真实数据要先在另一个终端运行 `.\start.bat`，等采集服务起来后再执行：

```powershell
.\.venv\Scripts\python.exe -m trend_analysis.run --once
```

持续每分钟同步并重算：

```powershell
.\.venv\Scripts\python.exe -m trend_analysis.run --loop --interval-seconds 60
```

消费者名字沿用 Person 1 的 `trend_live`。三次游戏分开计算。

## 给看板的文件

| 文件 | 内容 |
| --- | --- |
| `analysis/trends_latest.json` | 三个游戏的最新结果 |
| `analysis/games/<app_id>/trends_latest.json` | 单个游戏的最新结果 |

每个游戏看 `data_status` 和 `conclusions_publishable`。只有 `conclusions_publishable` 为 true 时，`trending_terms` 才是可以展示的趋势名单。词的明细在 `trends` 里，按强度从高到低排列。
