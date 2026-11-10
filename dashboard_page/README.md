# Steam Review Monitor

按 V2 设计图搭建的 Streamlit 演示页面。支持切换 CS2、Dota 2 和 Phantom Blade Zero，查看评论、情绪、关键词和提醒。

前后端联调约定见 [接口文档](API_SPEC.md)。

## 本地运行

在 dashboard_page 目录中使用 Python 3.11.9：

    python3.11 -m venv .venv
    .venv/bin/python -m pip install -r requirements.txt
    .venv/bin/python -m streamlit run app.py

已有项目虚拟环境时，直接运行最后一行。终端会打印本地访问地址，默认为 http://localhost:8501。

## 演示数据

demo_data.py 定义了三个游戏的模拟快照。采集时间、评论和关键词出现率均为示例，页面可离线演示。CS2 和 Dota 2 封面来自 Steam 商店。

- 总评论量、情绪图和情绪分布按所选游戏及时间范围统计。评论区展示所选游戏最近一小时的示例，和提醒的当前窗口对应。
- 关键词榜始终比较所选游戏最近一小时与此前 24 小时。
- 提醒比较所选游戏在上述两个时间窗口的负面占比。演示规则是两个窗口各至少 20 条已分类评论，且负面占比增加至少 15 个百分点。
