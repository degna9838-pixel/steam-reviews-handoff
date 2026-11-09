# 工作2：实体情感与显著变化检测 — 数据接入与开发指南

你负责识别提及所跟踪实体的评论，对各实体估计正面/负面/中性，并检测随时间的情感变化。现有采集清洗代码已提供输入，你只需通过 API 接入并在独立下游模块实现算法。

主要输入为完整 text_clean、created_at、usable_for_sentiment，同时使用 app_id、review_id、revision、pipeline_version。保留 text_raw 方便审查；tokens_trend 不适合拼回情感输入，steam_recommended 不能当情感真值。每款游戏单独定义实体/别名及统计窗口，未提及不等于中性，多实体态度分别估计。

本包尚未实现实体识别、情感模型或变化检测。消费者示例只负责可靠读取和持久化；词典/LLM、变化判据和输出由你实现，AGENT_PROMPT.md 已写明任务和验收。

## 先读这三份文件

1. 本README：安装、启动和角色输入。
2. AGENT_PROMPT.md：可整段复制给你的agent，要求它完成接入与本角色算法。
3. docs/API_GUIDE.md 和 docs/DATA_DICTIONARY.md：API、三游戏隔离、字段与增量更新约定。

这里“工作1/工作2”对应趋势检测/情感分析；项目原任务1/2（采集清洗）已完成。Python包名保留cs2_reviews，但支持三款游戏。

## 安装和启动

要求 Python 3.11–3.14，推荐3.12。首次安装需联网；macOS/Python3.12已实测，Windows提供脚本，尚未在实体Windows机器上验证。每次在解压后的项目根目录执行命令。

### macOS

终端A：

```sh
./setup.command
.venv/bin/python -m cs2_reviews doctor
./start.command --sample
```

如果解压工具丢失执行权限，可用 `bash setup.command`、`bash start.command --sample`。样例启动后，在终端B执行本指南的三游戏读取命令，消费者名称用 sentiment_demo。先在终端A按 Ctrl+C 停止样例，然后启动真实模式：

```sh
./start.command
```

### Windows PowerShell

终端A：

```powershell
.\setup.bat
.\.venv\Scripts\python.exe -m cs2_reviews doctor
.\start.bat --sample
```

样例启动后，在终端B执行读取命令。然后在终端A按 Ctrl+C 停止样例，再运行：

```powershell
.\start.bat
```

默认立即提供本机API http://127.0.0.1:8000，并在后台分别采集三款游戏。不要在启动服务的前台终端继续输入消费者命令；另开终端进入同一项目目录。已有这份采集服务运行时直接复用它。

## 分游戏验证读取

以下命令只验证接入，默认消费者只镜像数据和书签，尚未运行你的分析算法。macOS：

```sh
.venv/bin/python examples/consume_api.py --consumer sentiment_live --app-id 730 --once
.venv/bin/python examples/consume_api.py --consumer sentiment_live --app-id 570 --once
.venv/bin/python examples/consume_api.py --consumer sentiment_live --app-id 4115450 --once
```

Windows PowerShell：

```powershell
.\.venv\Scripts\python.exe examples/consume_api.py --consumer sentiment_live --app-id 730 --once
.\.venv\Scripts\python.exe examples/consume_api.py --consumer sentiment_live --app-id 570 --once
.\.venv\Scripts\python.exe examples/consume_api.py --consumer sentiment_live --app-id 4115450 --once
```

验证样例时将 sentiment_live 换为 sentiment_demo；样例是虚构输入，不能当实验结果。三款游戏可用相同消费者名称，因为路径仍按 app_id 隔离。持续读取时去掉 --once；需要并行处理三款时分别运行进程，或自行编写带独立状态的多游戏客户端。

换端口时补上 `--base-url http://127.0.0.1:18080`（与服务配置一致）。重复运行原命令会续跑，不要为日常重启添加 --reset。第一次接入真正算法请改用 sentiment_algorithm_v1 等新消费者，并建立空的分析表，避免接入测试已推进游标而漏算历史。

## 已提供的有用票和回复数

程序1.3.0的评论API同时输出votes_up与comment_count，原有text_raw、text_clean、tokens_trend和app_id保持。两项为非负整数或null，未知不是0。互动数变化会产生新的revision/upsert，更新同一评论，不增加新发布数量。旧数据升级会先备份，再从原始JSON补字段；保留原stream和进度，原消费者沿用游标。

两项是最近采到的快照；旧评测主要随每日全范围校对刷新，不能保证全部互动数10分钟更新。详情见docs/API_GUIDE.md。清洗算法仍为clean-en-v3，字段结构版本仍为1.0；严格禁止新增字段的自定义模型需同步更新。

## 本角色需要交出的结果

分游戏、分实体的逐评论标签，以及各窗口正/负/中性数量、提及量、变化指标/判定、算法版本、计算时间和处理水位。

三游戏必须保持独立，全部结果带app_id。先读取 /v1/games，按CS2=730、Dota 2=570、影之刃零=4115450选择对应数据。每次显式传入游戏，勿依赖默认CS2。完整业务约束和建议初版参数见 AGENT_PROMPT.md；这些是下游开发要求，不是采集端已经算好的字段。

## 交付代码范围

- cs2_reviews/：保持原样的采集、清洗、数据库与API。
- examples/consume_api.py：标准库增量消费者与事务回调，尚未绑定分析算法。
- examples/synthetic_reviews.json：虚构样例源，不能当真实实验数据。
- config.json：默认三游戏、30天首次回填、600秒目标采集间隔、端口8000。
- setup/start脚本与requirements：各自电脑安装启动，不分享虚拟环境。
- tests/：采集清洗与数据接入测试，安装requirements-dev.txt后可运行。

本包只包含本角色所需的代码和接入资料。历史开发报告、个人工具、已有真实数据库、固定快照、日志、缓存及虚拟环境均未打包。
