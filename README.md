# Social Persona AI (MVP)

一个从零实现的公用非隔离拟人社交 AI 后端原型，包含：

- FastAPI 网关与聊天接口
- 身份识别与会话管理
- 会话情绪 + 全局情绪偏移
- 记忆检索（当前用户 + 他人模糊摘要）
- 模糊化写入与事实记忆抽取
- SQLite 单文件存储 + 内置向量检索（低流量场景）
- OneBot 11 WebSocket 接入（QQ 私聊）
- 可切换 LLM Provider（mock / Kilo Gateway）

## 运行

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
uvicorn app.main:app --reload
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
