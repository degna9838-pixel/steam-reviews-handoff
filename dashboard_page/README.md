# Steam Review Monitor 前端

Streamlit 页面，仅从后端 HTTP API 读取数据。支持游戏切换、6小时/24小时/7天筛选、手动刷新、情绪趋势图与悬停提示、饼图、词云、趋势词表格、告警和评论弹窗。

## 文件结构

```text
dashboard_page/
├── app.py                 # 页面与图表渲染
├── data_source.py         # HTTP 请求、响应基本校验、接口错误处理
├── styles.css             # 页面样式
├── start_api.command      # 启动入口
├── requirements.txt       # Python 依赖
├── .streamlit/config.toml # Streamlit 主题
├── assets/                # 游戏封面
├── API_SPEC.md            # 后端数据契约
└── README.md
```

Python 3.11.9；`.venv` 是本地运行环境，`__pycache__` 是 Python 自动生成的缓存，均不提交 Git。

## 启动

先启动采集 API 和统一总览 API，再在此目录执行：

```bash
bash start_api.command
```

浏览器访问 http://localhost:8501。首次安装环境：

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
bash start_api.command
```

也可直接运行 `.venv/bin/python -m streamlit run app.py`，使用相同的默认后端地址。

## 后端接口

| 环境变量 | 默认地址 | 请求 |
| --- | --- | --- |
| `DASHBOARD_COLLECTOR_URL` | `http://127.0.0.1:8200` | `GET /v1/games` |
| `DASHBOARD_OVERVIEW_URL` | `http://127.0.0.1:8210` | `GET /v1/dashboard/overview?app_id=…&hours=…` |

更换服务地址：

```bash
DASHBOARD_COLLECTOR_URL=http://127.0.0.1:8000 \
DASHBOARD_OVERVIEW_URL=http://127.0.0.1:8100 \
bash start_api.command
```

所有评论、统计和趋势词均来自接口。后端通过 `status.mode=demo/live` 区分样例和真实数据，前端不生成模拟数据。当前联调使用 `steam-reviews-dashboard-backend` 中的新总览服务；后端演示库及数据生成命令见其 `steam_reviews/sentiment_analysis/docs/DASHBOARD_OVERVIEW.md`。

“立刻刷新”重新获取当前筛选范围的最新结果，并显示 `Last refresh` 时间。总览请求失败时，若会话中已有同一筛选范围的成功结果，则继续显示并提示错误。按钮不触发采集或 LLM 调用，后端无更新时数值可保持不变。

响应字段及计数口径见 [API_SPEC.md](API_SPEC.md)。
