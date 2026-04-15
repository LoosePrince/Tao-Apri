# 公用非隔离拟人社交 AI 架构说明

## 模块边界

- `api`: 对外接口 (`/chat`, `/session`, `/health`)
- `domain.services.identity_service`: 用户身份与会话生命周期
- `domain.services.persona_engine`: 稳定人设与时间语境
- `domain.services.emotion_engine`: 会话情绪与全局偏移
- `domain.services.memory_writer`: 消息模糊化、事实抽取、向量写入
- `services.chat_orchestrator`: 主编排流程
- `services.prompt_composer`: 提示词上下文拼装
- `repos`: 数据访问抽象及 SQLite + 向量检索实现

## 对话主流程

1. 用户请求进入 `/chat`
2. 识别/创建用户与会话
3. 基于当前消息更新情绪状态
4. 写入用户消息（raw + sanitized）
5. 检索记忆（当前用户 + 关联用户的模糊化记忆）
6. 组装提示词上下文
7. 生成回复并写入记忆
8. 返回回复与情绪状态

## 非隔离记忆原则

- 原始消息保留在 `messages` 表中的 `raw_content`
- 跨用户检索以 `sanitized_content` 与向量索引为主
- 对他人相关内容输出时只允许模糊摘要
- 向量检索默认仅覆盖最近 `N` 天（可配）

## 情绪持久化

- 全局情绪偏移持久化在 `emotion_state` 表
- 服务重启后自动恢复全局情绪基线

## 可演进方向

- 在高并发场景迁移到 PostgreSQL + Qdrant/pgvector
- 接入真实 LLM Provider 并分离模板层
- 增加情绪聚合定时任务与监控指标
