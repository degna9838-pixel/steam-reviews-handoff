# 核心评论字段

API 的 `review` 与 JSONL 一行结构相同。UTF-8；时间统一 UTC ISO 8601；ID 为字符串；数组和布尔值保留 JSON 类型。

| 字段 | 类型 | 含义 |
|---|---|---|
| schema_version | string | 当前为 1.0 |
| source | string | steam，样例通过独立数据库和模式区分 |
| app_id | integer | 正整数；CS2 730、Dota 2 570、影之刃零 4115450，须匹配请求游戏 |
| review_id | string | 来源 recommendationid，稳定评论 ID |
| created_at | string | 平台发布时间 |
| updated_at | string | 平台最后修改时间 |
| first_seen_at | string | 本地首次观察时间 |
| last_seen_at | string | 本地最近观察时间，不产生新评论事件 |
| language | string | 来源语言标签 |
| language_check | string | passed / uncertain / rejected，非人工审定 |
| text_raw | string | 原始正文 |
| text_clean | string | 保留语义的轻度清洗正文 |
| tokens_trend | array[string] | 趋势词项，保留出现顺序和重复次数 |
| steam_recommended | boolean | 来源 voted_up，与文本情感不同 |
| votes_up | integer / null | 最近一次观察到的“认为这条评测有用”的人数；0为真实零，null表示未知 |
| comment_count | integer / null | 最近一次观察到的评测下评论/回复数量；不包含回复正文；0与null不同 |
| quality_flags | array[string] | 本地检查发现的问题，无问题为 [] |
| usable_for_trend | boolean | 趋势模块是否纳入 |
| usable_for_sentiment | boolean | 情感模块是否纳入 |
| pipeline_version | string | 当前 clean-en-v3；历史变更可含已知v1/v2 |
| revision | integer | 本地版本，从 1 开始，相关来源内容/清洗结果变化后递增 |

原有必填核心字段不使用 null；缺失来源 ID、时间、正文或推荐标签会使该页失败并保留进度，不会编造默认值。1.3.0在schema_version=1.0下兼容扩展votes_up/comment_count：来源缺失或为null时保留null；负数、布尔值、字符串等非法计数使整页失败并保留进度，不将未知补成0。旧历史事件没有这两个字段时，API响应显示null；消费者也应允许字段缺失或null。

`votes_up` 与 `steam_recommended` 不同：前者是别人认为评测有用的数量，后者是评测作者是否推荐游戏。它们都不是文本情感真值。两项互动数来自匿名评论接口，无需新增API Key；本次没有加入昵称、头像或作者身份字段。

互动数为最近采到该评测时的快照，不是实时平台读数。每10分钟的增量轮询不能保证所有旧评测互动数同时刷新，因为互动可能不改变来源timestamp_updated；旧评测主要随每日全项目范围校对刷新，网络、分页和缓存还会造成延迟。可结合last_seen_at显示最近观察时间；只有观察时间变化不会追加事件。计数可因撤票/删除回复等下降，应保留实际返回值，不强制单调增加。

## 质量标记

| 标记 | 含义 |
|---|---|
| `no_textual_content`、`empty_text`、`no_trend_tokens` | 无有效文字、空正文或没有可用趋势词 |
| `api_language_not_english`、`non_english_text` | 来源语言不符或正文判为明显非英文 |
| `short_or_domain_text`、`language_detection_uncertain` | 短文本/术语或语言证据不足 |
| `mixed_language_suspected` | 存在非拉丁字母或疑似混语证据；不等于人工确认混语 |
| `mixed_language_sentiment_excluded` | 混语证据使完整正文不适合作普通英语情感输入 |
| `unclear_semantics` | 当前无法确认短文本语义，不猜改为英文词 |
| `unicode_style_normalized` | 定向规范了数学花体等显示形式 |
| `ascii_art_suspected`、`ascii_art_removed` | 发现图案或已在清洗视图移除，原文仍保留 |
| `vote_farming_suspected`、`low_relevance_suspected` | 本地规则提示求赞/低相关内容 |

语言状态和标记都来自启发式检查，不是人工真值。有效英文短评可以 `uncertain` 但允许分析；不明短串则保留记录并排除。混语不自动提取英语片段：可靠英语主体可供趋势，实质外语段排除普通英语情感输入；少量外语且有专名或括号英文释名上下文可保守留用。不能仅凭字符比例保证它是无害专名。

分别依据 `usable_for_trend`、`usable_for_sentiment`；不能将全部 `uncertain` 一律放行或删除，也不能把某一 flag 的数量当成人工确认的语言数量。

`quality_flags` 不包含 Steam 逐条偏离主题结论，因为接口没有提供该字段。

## 文本示例（虚构）

```text
原文：[b]CS2[/b] is fun, but VAC is not good. Servers are lagging!
清洗：CS2 is fun, but VAC is not good. Servers are lagging!
词项：["cs2", "fun", "vac", "not", "good", "server", "lag"]
```

情感/实体/方面分析读取 `text_clean`，不使用删过停用词的列表。过滤后相邻词项可能不在原句紧邻；短语检测应从完整清洗文本重新分词或采用自己的规则。

v2 保留有意义的换行/列表和正文数字；按行移除高置信字符画，定向转换花体。趋势中将 `anti cheat / anti-cheat / anticheat` 统一为 `anticheat`，但 `cheater`、`cheat`、`vac` 各自保留。一般词形用英语词典，领域映射与专名、常见缩写、比较强度词保护优先；不做开放式纠错，仍可能有上下文歧义。

趋势排除评分 `x/y`、百分比、裸整数；保留 `CS2`、`AK-47`、`128-tick`、`1.6`、明确版本等标识，有限处理连写数量单位。原文和清洗正文仍保留数值。额外领域停用词、短语识别和词频/文档频率口径由分析模块记录算法版本。

## 变更事件

事件包含 `change_id`（递增序号）、`operation="upsert"`、`review_id`、`revision` 与该版本完整 `review`。

同一 ID 可以有多个事件；不同 ID 正文相同仍为独立评论。正文、来源修改时间、推荐选择、清洗规则结果或votes_up/comment_count变化都会产生revision/upsert；仅作者游玩时长或观察时间变化不产生分析事件。互动数变化不会改写created_at/updated_at，也不增加新发布评论数量；revision是整条公开记录的版本，不专指文本编辑次数。

变化历史中的 `last_seen_at` 是该版本事件发生时的观察时间；当前 API 记录会继续更新最近观察时间。

重清洗追加新版本和 upsert，保留评论 ID、`stream_id`、创建/来源修改/首次观察/最近观察时间；事件的处理时间不冒充新发布时间或采集时间。可用性 true→false 也要撤销旧贡献，不能忽略这类更新。历史事件不会回写成当前v3；消费者需支持已知v1/v2/v3。

## 导出清单

`reviews_current.jsonl` 配套 `reviews_current.manifest.json`；样例用 `sample_reviews_current` 前缀。

| 字段 | 含义 |
|---|---|
| `schema_version`、`stream_id` | 字段结构及所属游戏的数据流 |
| `app_id` | 快照所属游戏，须与所有记录、消费请求匹配 |
| `mode` | `live` 真实或 `sample` 虚构样例 |
| `pipeline_versions` | 此快照实际存在的清洗版本 |
| `exported_at` | 导出时间，UTC |
| `scope_start_at` | 项目创建时间范围下界，尚未建立范围时可为 null |
| `backfill_status` | 回填状态，尚无任务时可为 null |
| `snapshot_cursor` | 同次快照对应的增量水位 |
| `row_count` | JSONL 评论数 |
| `jsonl_file`、`jsonl_sha256` | 配套文件名及原始字节 SHA-256 |

行、元数据和水位来自同一数据库读事务。两个文件分别原子替换；读取方必须先校验哈希、行数和版本，失败则重读配对文件，成功后才能用其游标。清单不保证来源无遗漏或语言判断完全正确。

## 多游戏归属

保留原有字段与schema_version=1.0，1.3.0兼容增加可空votes_up/comment_count。各游戏数据库、stream_id、revision 和 cursor 独立；统一分析库用 (app_id, review_id) 识别评论。名称从 /v1/games 获取，不根据正文猜测。

CS2 导出在 data/，其他游戏在 data/games/<app_id>/。消费者同时检查 app_id、stream_id 和清洗版本；旧manifest缺少app_id时逐记录核对，空旧快照不允许初始化非默认游戏。错游戏、错流或未知版本不能推进状态。
