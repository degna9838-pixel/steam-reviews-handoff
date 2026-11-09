# 工作2：实体情感与显著变化检测：复制以下提示词给 Agent

```text
你收到的是已经实现Steam英文评论采集和清洗的Python项目，我负责的是下游工作2：实体情感与显著变化检测。请先读README.md、docs/API_GUIDE.md、docs/DATA_DICTIONARY.md，按以下要求自主完成本角色的数据接入和算法开发。

先安装启动并验证数据，不要只写方案。优先Python3.12，使用项目自己的虚拟环境。macOS使用setup.command/start.command，Windows PowerShell使用.\setup.bat/.\start.bat。所有新增代码、结果、日志保存在该项目目录内。现有cs2_reviews/采集清洗模块、requirements和启动脚本保持不变，在独立下游模块扩展。只实现本角色的数据分析与结果接口，服务和分析在组员本机运行，不依赖原开发者电脑。

一、必须按游戏独立处理
1. CS2=730，Dota 2=570，影之刃零Phantom Blade Zero=4115450。通过GET /v1/games核对目录，所有health/reviews/changes/单条API请求显式传app_id，所有示例消费者显式传--app-id；省略默认CS2，不能省略。
2. 每游戏独立stream_id/cursor、分析结果、窗口分母与角色配置。用(app_id,review_id)关联评论，实体任务再加entity_id；不能合并三游戏的词频或情感统计。正文提到另一游戏也不改变来源app_id。响应逐条验证app_id，错游戏、错流或未知版本停止，不推进失败批次。
3. 默认配置会同时采三游戏。API中的app_id仅选择读取对象，不需要修改爬虫。使用运行中同一份API即可；最终同机整合只启动一份采集服务，两种分析分别消费它。

二、接入验收
1. 新环境安装依赖并运行doctor。先--sample验证虚构样例，health为offline/disabled；停止后不带--sample启动真实采集，health为live。source=steam本身不能区分样例与真实。用不同消费者名称隔离demo和live。
2. 三游戏分别通过API读取评论并核对health、app_id、stream_id、字段、pipeline_versions和回填状态；不给不同游戏拼接输入。数据暂空时检查data_ready/last_error，勿伪造记录。
3. 先用examples/consume_api.py --consumer sentiment_live --app-id <ID> --once验证，重跑验证保存游标，去掉--once可持续轮询。API默认http://127.0.0.1:8000，端口改变时必须同时传--base-url，消费者不会自动读config.json。不要每次启动重置状态。
4. 正式输入用/v1/reviews/changes?app_id=<ID>&cursor=<cursor>&limit=100，从0重放。items是事件，items[].review是完整评论；当前reviews接口只便于抽查，过滤后的当前列表不能代替changes。支持已知clean-en-v1/v2/v3历史事件，追到当前水位后统一当前清洗版本，未知版本立即停止。
5. 对每条upsert按ID/revision幂等更新，先减旧贡献再加新贡献；usable标记true变false也撤销旧贡献。按UTC created_at归发布时间窗口，不能把回填/修改/重清洗当新发布。分析结果、记录与游标成功提交后才能推进；复用save_batch(...,app_id=...,on_upsert=...)或等价事务语义。
6. 示例消费者只保存数据与进度，并不执行分析。第一次接入算法使用新的sentiment_algorithm_v1消费者和空分析表，或全量重建历史。默认--reset不会替你重置新增分析表。
7. 首次目标回填30天，未完成分页会续跑，完成后约600秒目标间隔采集；Steam可能缓存，属于近实时。开发可先开始；backfill未完成、历史不足、样本不足或源数据陈旧时标注状态，不把缺失当零或发布完整窗口结论。质量标记是启发式，以本角色对应usable字段决定纳入，不按uncertain一刀切。

补充：程序1.3.0已提供votes_up（认为评测有用的人数）和comment_count（评测下回复数）。它们为非负整数或null，保留给后续结果消费者；未知不能当0，样例值虚构。互动数变化也产生revision/upsert，不是新评论；文本/可用性/清洗及算法配置不变时可复用旧分析结果。旧评测互动数主要随每日全范围校对刷新，不保证所有计数每10分钟更新。原有游标继续使用，旧历史事件没有计数时允许缺失/null。

三、本角色：跟踪实体的情感与变化检测
1. 直接读取完整text_clean、created_at、usable_for_sentiment，保留text_raw用于审查以及review_id/revision/pipeline_version。不要把tokens_trend拼回句子，不额外去停用词破坏否定；steam_recommended不是文本情感真值，禁止直接映射标签或当作人工评估真值。
2. 每游戏分别建立跟踪实体/方面及别名配置，例如游戏名、特定系统或玩法方面；明确匹配规则、大小写/词边界和别名，不按评论提到其他游戏改来源。未提及某实体的评论不计入该实体统计，不能当中性。多实体评论分别估计针对各实体的态度，避免整体好评覆盖局部抱怨。无需等待趋势模块输出才能工作；首版先采用明确、可配置的实体列表。
3. 实现基于词典或LLM API的正面/负面/中性分类。未提供模型密钥时优先选择可本地运行的词典初版，不把接入卡在密钥上；说明否定、程度、转折、俚语、反讽、多实体和混语限制。若增加新依赖放独立分析requirements文件，不修改采集端锁定依赖。LLM密钥仅从环境读取、不写入代码或包，不伪造概率/置信度。
4. 实现每游戏、每实体的时间变化检测：按UTC created_at聚合，可先比较[T-1h,T)与[T-25h,T-1h)，窗口和分母独立。保存正/负/中性数量、提及量、所用情感指标、变化量、样本量与判定参数；设置可配置最小样本及变化阈值。若声称统计显著，必须给出检验、假设和适用条件；只用阈值时称阈值告警，不冒充统计显著性。
5. 逐评论实体结果建议含app_id、review_id、revision、entity_id、sentiment_label、可选score/理由、created_at、pipeline_version、algorithm_version、analyzed_at；汇总建议含app_id、entity_id、window/baseline边界、positive/negative/neutral_count、mention_count、metric、change、change_flag、computed_at、source_stream_id、processed_cursor、data_status。没有实际概率来源时不输出概率。
6. 耗时LLM可事务外预计算并按(app_id,review_id,revision,entity_id,algorithm_version)幂等缓存，成功写入结果前不得推进消费游标。对修改和true→false更新撤销旧实体贡献；按新版本重新匹配实体，实体不再出现时也撤销其旧计数。没有确定的分类结果不得默默归中性或推进为已分析，应记录待处理/错误状态。
7. 分游戏结果放analysis/games/<app_id>/等隔离目录，或使用始终带app_id的结果表；分别保存实体配置。验证未提及、多实体相反态度、否定、短评、修改、实体消失、不可用更新、重复批次和跨游戏隔离。给出可持续运行入口及分游戏结果契约；本次不制作图表。

四、完成时报告
给出实际执行命令、三游戏各自读取数量和回填状态、游戏隔离与失败回滚验证、重跑和重启游标证据、自己的算法启动命令、算法选择/参数/限制、分游戏结果路径。提供可复现的小样本验证和一条真实评论的输入输出。没有数据或算法未完成时如实报告，不把虚构样例或消费者镜像说成真实分析结果。

```
