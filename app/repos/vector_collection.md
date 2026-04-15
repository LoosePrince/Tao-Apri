# SQLite Vector Definition

当前默认实现使用 `SQLite` 单库 + `vector_index` 表：

- `message_id`: TEXT PRIMARY KEY
- `user_id`: TEXT
- `related_user_ids`: JSON string array
- `sanitized_content`: TEXT
- `embedding`: JSON float array

说明：

- 向量由应用层轻量 embedding 函数生成并归一化。
- 检索时在应用层执行 cosine 计算并按分数排序。
- 相关用户 (`user_id` 命中或 `related_user_ids` 包含当前用户) 会得到额外分数加权。

迁移建议（流量增大后）：

1. 保持同样 payload 语义。
2. 将 `embedding` 从 JSON 字段迁移到专业向量库（Qdrant/pgvector）。
3. 业务层仅替换 `VectorRepo` 实现，不改上层编排逻辑。
