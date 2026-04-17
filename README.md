# Tao Apri

基于 `FastAPI + SQLite` 的拟人社交 AI 后端原型，当前包含会话管理、情绪聚合、记忆检索、LLM 网关接入，以及 HTTP / OneBot 11 通道能力。

## 核心功能

- 聊天主链路：身份识别、会话状态、窗口预处理、人格生成
- 情绪系统：会话情绪 + 全局情绪偏移 + 定时聚合任务
- 记忆系统：事实抽取、去敏写入、向量召回（低流量单机场景）
- 模型接入：Kilo Gateway（不可用时返回统一不可用提示，不回退 mock）
- 通道接入：HTTP API + OneBot 11 WebSocket（QQ 私聊 / 群聊）

## 快速开始

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
Copy-Item .env.example .env
uvicorn app.main:app --reload
```

或使用启动脚本：

```powershell
.\start.ps1
```

默认 SQLite 文件：`social_persona_ai.db`（项目根目录）。

## 常用命令

```powershell
python -m pytest
python scripts\sync_env_defaults.py
python scripts\package_release.py
```

## 主要接口

- `GET /health`
- `GET /metrics`
- `POST /chat`
- `GET /session/{user_id}`
- `GET /llm/models`

## 目录说明

- `app/`：业务代码（API、领域服务、存储、任务调度）
- `prompt_assets/`：提示词、人设与规则词表
- `scripts/`：环境同步与发布打包脚本
- `docs/`：补充文档（配置、接口、架构）

## 文档入口

- 配置项：`docs/configuration.md`
- 接口说明：`docs/api.md`
- 架构说明：`docs/architecture.md`
