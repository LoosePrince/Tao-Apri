# Social Persona AI (MVP)

一个从零实现的公用非隔离拟人社交 AI 后端原型，包含：

- FastAPI 网关与聊天接口
- 身份识别与会话管理
- 会话情绪 + 全局情绪偏移
- 记忆检索（当前用户 + 他人模糊摘要）
- 模糊化写入与事实记忆抽取
- SQLite 单文件存储 + 内置向量检索（低流量场景）
- OneBot 11 WebSocket 接入（QQ 私聊）
- Kilo Gateway 模型接入（失败时返回统一不可用提示，不回退 mock）

## 运行

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
uvicorn app.main:app --reload
```

或使用一键脚本：

```powershell
.\start.ps1
```

默认会在项目根目录创建 `social_persona_ai.db`。

## 配置

```powershell
Copy-Item .env.example .env
```

复制后按需编辑 `.env`。完整字段见 `docs/configuration.md`。

## 测试

```powershell
python -m pytest
```

## 日志

- 终端：`INFO` 及以上
- 正常日志文件：`logs/app_info_YYYYMMDD_HHMMSS.log`（每次启动新文件，永久保留）
- Debug 详尽日志：`logs/app_debug_latest.log`（每次启动覆盖）
