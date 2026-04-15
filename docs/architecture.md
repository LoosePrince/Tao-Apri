# 公用非隔离拟人社交 AI 架构说明

## 模块边界

- `api`: 对外接口（`/chat`、`/session`、`/health`、`/metrics`、`/llm/models`）
- `domain.services.identity_service`: 用户身份与会话生命周期
- `domain.services.persona_engine`: 稳定人设与时间语境
- `domain.services.emotion_engine`: 会话情绪与全局偏移
- `domain.services.memory_writer`: 消息模糊化、事实抽取、向量写入
- `services.chat_orchestrator`: 主编排流程（检索规划、跨对话访问控制、画像更新）
- `services.conversation_window_manager` + `services.window_preprocessor`: 多条输入合并与窗口压缩
- `services.prompt_composer`: 提示词上下文拼装
- `jobs.task_queue` + `jobs.periodic_scheduler`: 异步任务与定时维护
- `repos`: 数据访问抽象及 SQLite + 向量检索实现（含关系、偏好、画像、情绪状态）

## 对话主流程

1. 用户请求进入 `/chat`（由窗口管理器合并输入批次）
2. 识别/创建用户与会话
3. 基于当前消息更新情绪状态
4. 写入用户消息（raw + sanitized）
5. 多轮检索规划与记忆召回（当前用户 + 跨用户候选）
6. 基于关系/偏好策略筛选跨对话可见记忆
7. 生成/更新用户画像摘要并注入上下文
8. 组装提示词上下文并调用 LLM 生成回复
9. 写入 assistant 消息，异步更新关系状态
10. 返回回复与情绪状态

## 非隔离记忆原则

- 原始消息保留在 `messages` 表中的 `raw_content`
- 跨用户检索以 `sanitized_content` 与向量索引为主
- 对他人相关内容输出时只允许模糊摘要
- 向量检索默认仅覆盖最近 `N` 天（可配）
- 不设计“诱导泄露”系统机制；跨对话能力仅用于主题级概览

## 跨对话查询设计约束（2026-04-15 更新）

- 目标：允许 AI 参考其它对话，但只能返回“概况”，且是否可检索由关系与偏好决定。
- 实现策略：
  - 提示词中明确跨对话输出边界（只可概述，不可给细节）
  - 编排阶段优先使用 `sanitized_content` 与事实摘要，不拼接 `raw_content`
  - 引入关系准入：无关系返回“不知道”，有关系按强度与极性决定检索宽松度
  - 引入偏好过滤：按用户公开偏好控制可被检索的话题范围
  - 引入画像注入：由 AI 生成/更新用户画像，用于调整发言风格与信息详略
- 输出边界：
  - 允许：群体趋势、共性问题、情绪倾向等主题级信息
  - 禁止：用户标识、联系方式、精确时间地点、原句复述、可反查事件片段

## 关系与偏好驱动检索（已实现）

- 关系模型（user_a -> user_b）：
  - `relation_polarity`: `positive | neutral | negative`
  - `relation_strength`: `0.0 ~ 1.0`
  - `trust_score`: `0.0 ~ 1.0`
- 检索决策：
  - 无关系或低强度负关系：不检索跨对话，回复“不知道”
  - 正向关系：放宽召回阈值并允许更多主题摘要
  - 负向关系：收紧召回阈值，只返回最泛化概况或拒答
- 偏好模型（每个用户）：
  - `share_preference_default`: `allow | deny`
  - `topic_visibility`: 话题级可见性（如学习可见、家庭不可见）
  - `explicit_deny_items`: 明确不可共享事项
- 画像模型（AI维护）：
  - 画像字段包含兴趣倾向、表达偏好、敏感话题倾向、关系倾向
  - 在每轮对话后更新画像摘要，供下一轮提示词注入

## 情绪持久化

- 全局情绪偏移持久化在 `emotion_state` 表
- 服务重启后自动恢复全局情绪基线

## 定时任务与维护

- `jobs.maintenance_enabled=true` 时启用定时调度
- 当前内置任务：
  - 情绪聚合任务（按时间窗口汇总最近消息）
  - 向量维护任务（向量索引/热度维护）

## 可演进方向

- 在高并发场景迁移到 PostgreSQL + Qdrant/pgvector
- 接入真实 LLM Provider 并分离模板层
- 完善运行指标观测与告警
