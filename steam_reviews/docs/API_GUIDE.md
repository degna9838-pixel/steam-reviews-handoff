# 数据 API 接入约定

## 数据源和三游戏隔离

本服务已实现英文评论采集和清洗。分析模块只需通过本机 API 读取，无需再爬 Steam、读取原始来源 JSON 或重复清洗。

| 游戏 | 必须显式传入的 app_id | 服务端真实库 | 同名消费者的本地副本 |
|---|---:|---|---|
| CS2 | 730 | data/reviews.sqlite3 | data/consumer_<名称>.sqlite3 |
| Dota 2 | 570 | data/games/570/reviews.sqlite3 | data/games/570/consumer_<名称>.sqlite3 |
| 影之刃零 / Phantom Blade Zero | 4115450 | data/games/4115450/reviews.sqlite3 | data/games/4115450/consumer_<名称>.sqlite3 |

配置 app_ids 默认包含三款游戏；后台分别采集，数据库、采集检查点与数据流独立。请求 app_id 选择读取哪一款，不会重新触发采集，也不会改变其他游戏的采集状态。正文提到其他游戏不会改变评论所属游戏。

**每次 API 请求显式传 app_id，每次消费者命令显式传 --app-id。省略时默认 CS2，不能用默认值代表当前选择。** 每个游戏独立保存 stream_id/cursor、实体或词项配置、窗口分母和分析结果；结果必须包含 app_id。不能把三款评论合在一起算词频、情感比例或显著变化。

服务端按游戏选择独立数据库。示例消费者逐条验证 app_id，并绑定数据流；错游戏或错流时停止，不推进失败批次。若自行编写客户端，必须保留这些检查。统一结果库中评论身份至少使用 (app_id, review_id)，不共享跨游戏游标。

## 仅通过 API 起步

本节的 python 是虚拟环境解释器：macOS 为 `.venv/bin/python`，Windows PowerShell 为 `.\.venv\Scripts\python.exe`，无需激活环境。

默认 base URL 为 `http://127.0.0.1:8000`。每位组员在自己的机器安装运行，不依赖开发者电脑在线。最终同一台电脑整合时只启动一份采集服务，两个分析模块连接这份服务；各自独立开发时的数据集合可能不同，不能直接拼接成同一份实验结果。

| GET 请求 | 用途 |
|---|---|
| /v1/games | 游戏目录、启用状态、各自条数和数据流 |
| /v1/health?app_id=570 | 指定游戏的模式、版本、数量、回填和采集状态 |
| /v1/reviews?app_id=570&limit=5 | 该游戏当前完整评论，便于抽查 |
| /v1/reviews?app_id=570&usable_for=trend&limit=5 | 抽查趋势可用记录 |
| /v1/reviews?app_id=570&usable_for=sentiment&limit=5 | 抽查情感可用记录 |
| /v1/reviews/changes?app_id=570&cursor=0&limit=100 | 持续处理新增和修改，正式接入入口 |
| /v1/reviews/评论ID?app_id=570 | 按 ID 读取该游戏当前记录 |

limit 范围 1–1000。未知游戏返回 422，已支持但未启用游戏返回 503，未知评论返回 404，游标超过当前流范围返回 409。不能遇到错误就自动换游戏或跳过批次。

用标准库检查 API 的最小示例（保存为自己的脚本，项目目录执行）：

```python
import json
from urllib.parse import urlencode
from urllib.request import urlopen

BASE_URL = 'http://127.0.0.1:8000'
for app_id in (730, 570, 4115450):
    query = urlencode({'app_id': app_id})
    with urlopen(f'{BASE_URL}/v1/health?{query}', timeout=30) as response:
        health = json.load(response)
    if health['app_id'] != app_id:
        raise ValueError('Wrong game health response')
    query = urlencode({'app_id': app_id, 'limit': 5})
    with urlopen(f'{BASE_URL}/v1/reviews?{query}', timeout=30) as response:
        page = json.load(response)
    if any(review['app_id'] != app_id for review in page['items']):
        raise ValueError('Cross-game record')
    print(app_id, health['mode'], health.get('backfill_status', 'not_started_or_not_applicable'), len(page['items']))
```

`reviews.items` 是评论列表。`changes.items` 是事件列表，每个事件的 `review` 才是完整评论：

```text
changes page: schema_version, stream_id, items, next_cursor, has_more
    items[]: change_id, operation="upsert", review_id, revision, review
        review: app_id, review_id, created_at, text_raw, text_clean,
                tokens_trend, usable_for_trend, usable_for_sentiment, ...
```

页顶没有 app_id 字段；从请求游戏、health 和每条 review.app_id 核对。next_cursor 是十进制字符串，事件 change_id 为整数；沿用示例处理，不能把当前记录浏览游标 next_after_id 当作变更游标。

## 有用票和回复数（程序1.3.0）

所有评论API（当前列表、筛选列表、单条和changes事件中的review）均提供`votes_up`与`comment_count`，JSONL也一致。votes_up是认为评测有用的人数，comment_count是评测下评论/回复数量，不包含回复全文。它们都是严格非负整数或null：0是真实零，缺失/未知为null，不能用零替代未知，也不能把votes_up与作者是否推荐的steam_recommended混淆。

互动数单独变化也会追加revision/upsert；原文、发布时间和来源修改时间可以完全不变。下游仍按原评论ID更新，不增加发布量或重复累计词频/情感数量。文本、可用性、pipeline、实体匹配和算法配置未变时，可复用已有情感结果，无需因为投票变化再次付费调用LLM。当前记录的两个数值可以直接供后续展示读取，样例模式中的数值是虚构的。

两项是最近采到的快照。Steam的互动变化不一定更新timestamp_updated，旧评测主要随每日全范围校对刷新，不能承诺全量互动每10分钟更新。结合last_seen_at说明最近观察时间；允许数值真实下降。旧历史事件没有此字段时API显示null，自定义客户端也需允许缺失/未知。schema_version仍为1.0；若旧客户端严格禁止新增字段，需要更新模型。标准库示例已兼容并校验非法计数，失败批次不提交。

旧库启动时会检测缺字段，持锁备份后从原始JSON补齐，保留stream_id、检查点及来源/观察时间，追加新版本。旧changes不回写。无需--reset，原消费者从原游标继续。

## 持续接入与断点恢复

使用 examples/consume_api.py 先验证读取。它只保存最新评论和进度，没有趋势检测、实体识别或情感算法。`--once` 追到可见数据头后退出；去掉它会持续轮询，默认每60秒检查 API。采集的600秒目标间隔是另一个参数。

初次从 cursor=0 重放完整变化流；后续使用已成功保存的 next_cursor。按游戏和任务独立保存 app_id、stream_id、cursor，断线/进程重启沿用原状态。重复事件按 review_id/revision 幂等处理。409、错数据流、未知schema或未知清洗版本必须停止并排查，不能静默重置或前移游标。

**正式持续处理必须消费完整 changes。** 当前列表中的 usable_for 筛选仅供抽查，不能作为持续输入；某条记录由可用变为不可用，也会发出完整 upsert，必须撤销它的旧分析贡献。

已有记录修改时：撤销旧贡献，再按新版本与可用性写入；不增加新发布评论数。历史回填和重新清洗也不是新发布。所有发布时间窗口使用 UTC created_at；first_seen_at 是采集时间，updated_at 是来源修改时间，均不能替代发布时间。

### 接入自己算法的位置

示例的 `save_batch(db, batch, cursor, app_id=..., on_upsert=...)` 提供旧/新记录回调，评论、分析结果和游标在同一 SQLite 事务中提交，失败整批回滚：

```python
def update_analysis(db, previous, current):
    # 先撤销 previous 的贡献（可能为 None），再处理 current。
    # 按 app_id 分开分析表；在同一 db 中写分析结果，不自行 commit。
    # 不可用的新版本也要写状态并撤销旧贡献。
    pass

cursor, changed = save_batch(
    db, batch, cursor, app_id=selected_app_id, on_upsert=update_analysis
)
```

该片段是扩展示意，不是可以单独运行的分析算法；实际客户端仍要创建 reviews/state 表、请求批次并恢复游标，完整流程参照 examples/consume_api.py。在自己的下游客户端复用这些函数，保留原始采集清洗模块。

首次加入算法或更换算法版本，必须使用新的消费者与空分析表，或从本地完整评论全量重建。不能沿用已推进的纯接入测试游标后期待自动补算。`--reset` 只重置示例自带 reviews/state 两表，不会清理你新增的分析表；常规续跑不使用它。

## 模式、质量与时间范围

- 真实启动不带 --sample，health.mode=live；虚构样例启动带 --sample，health.mode=offline、collector_state=disabled。source 字段两者都为 steam，不能单凭它判断真假，必须检查服务模式。
- 第一次目标为启动前30天发布的评论，并持久化范围下界；之后持续保存这个项目范围和新评论。发布不足30天的游戏只有实际存在的评论。回填分页可能连续续跑，完成后目标开始间隔600秒，单游戏任务不重叠。Steam匿名缓存可能最多10分钟，不承诺发布后立即出现。
- 读取 health 的 ready、data_ready、mode、last_error、last_success_at、cleaning_ready、pipeline_versions、backfill_status 和 checkpoint。ready 不代表已有数据；cleaning_ready 在空库也可能为true，不能据此认定回填完成。backfill_status=running 时先开发，结果须标注历史未完整；completed 也不保证来源无遗漏。
- 当前 schema_version=1.0，清洗目标 clean-en-v3。历史 changes 可含已知 v1/v2；从0重放到当前水位后再发布统一版本统计。未知版本停止，不能跳过后推进游标。
- quality_flags 和 language_check 属于启发式检查，不是人工真值。分别使用任务对应 usable_for_*，不要仅按 uncertain 或某个flag一刀切。英文短评可以有效。
- 数据来源限定Steam筛选为英文的评论，再做本地质量检查。原文仍保存；语言检查和规范化不能保证100%准确，结果只代表采到的Steam评论。
- 本包不附固定真实快照，正常流程只读你本机的API。CLI虽保留 --snapshot 功能，但首次接入不需要；该选项导入后仍会连接API，只有同一数据库导出的快照才可接续原stream。样例和真实使用不同消费者名称。

## 运行与输出位置

服务端采集库按上表保存在 data/，日志在 logs/，临时文件在 work/。API 已同时提供 text_raw 与 text_clean，无需分析同学直接打开采集库。JSONL/manifest 导出属于可选功能，不是API接入前提。每条评论核心字段的类型与约定见 DATA_DICTIONARY.md。

关闭采集窗口或 Ctrl+C 会停止采集；重开同一项目目录保留服务端数据流和检查点。分析进程也可 Ctrl+C 停止，同名同游戏重开继续。已追平且本轮暂无新事件时可能打印 events=0；--once 随后退出，持续模式等待后继续轮询。服务仍在回填时重跑继续读到更多数据是正常现象。

若端口8000占用，先确认是否已有要共享的采集服务；否则在 config.json 修改 port（如18080），重启，并为每个客户端传 `--base-url http://127.0.0.1:18080`。示例消费者不会自动读取服务端配置。服务仅绑定本机；不同电脑应各自运行，最终合并开发应使用同一份源数据。
