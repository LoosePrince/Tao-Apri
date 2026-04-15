# Tao Apri

一个从零实现的公用非隔离拟人社交 AI 后端原型（FastAPI + SQLite），目前已包含：

- 聊天主链路：身份识别、会话状态、窗口预处理、人格生成
- 情绪系统：会话情绪 + 全局情绪偏移 + 定时聚合任务
- 记忆系统：事实抽取、模糊化写入、向量召回（低流量单机场景）
- 模型接入：Kilo Gateway（不可用时返回统一提示，不回退 mock）
- 通道接入：HTTP API + OneBot 11 WebSocket（QQ 私聊）

## 运行

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
uvicorn app.main:app --reload
```

或直接：

```powershell
.\start.ps1
```

默认数据库文件：`social_persona_ai.db`（项目根目录）。

## 配置

```powershell
Copy-Item .env.example .env
```

按需编辑 `.env`，完整字段见 `docs/configuration.md`。

## 接口（最小集合）

- `GET /health`：服务健康检查
- `GET /metrics`：运行指标快照
- `GET /session/{user_id}`：查询会话状态
- `POST /chat`：发送消息并获得回复
- `GET /llm/models`：查看当前模型连通与可用列表

## 测试

```powershell
python -m pytest
```

## 日志

- 终端输出：`INFO` 及以上
- 常规日志：`logs/app_info_YYYYMMDD_HHMMSS.log`（每次启动新文件）
- Debug 日志：`logs/app_debug_latest.log`（每次启动覆盖）
