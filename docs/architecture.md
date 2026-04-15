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
- 不设计“诱导泄露”系统机制；跨对话能力仅用于主题级概览

## 跨对话查询设计约束（2026-04-15 更新）

- 目标：允许 AI 参考其它对话，但只能返回“概况”，不能返回“可定位细节”。
- 实现策略：
  - 提示词中明确跨对话输出边界（只可概述，不可给细节）
  - 编排阶段优先使用 `sanitized_content` 与事实摘要，不拼接 `raw_content`
  - 回复前执行细节过滤，阻断可识别信息输出
- 输出边界：
  - 允许：群体趋势、共性问题、情绪倾向等主题级信息
  - 禁止：用户标识、联系方式、精确时间地点、原句复述、可反查事件片段

## 情绪持久化

- 全局情绪偏移持久化在 `emotion_state` 表
- 服务重启后自动恢复全局情绪基线

## 可演进方向

- 在高并发场景迁移到 PostgreSQL + Qdrant/pgvector
- 接入真实 LLM Provider 并分离模板层
- 增加情绪聚合定时任务与监控指标
