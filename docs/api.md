# API 概览

## `GET /health`

返回服务健康状态。

## `GET /metrics`

返回运行指标快照（计数/耗时等聚合指标）。

## `POST /chat`

请求体：

```json
{
  "user_id": "u_001",
  "message": "今天心情不错"
}
```

返回：

- `reply`: 拟人回复文本
- `session_emotion`: 当前会话情绪
- `global_emotion`: 全局情绪偏移
- `session_id`: 用户会话ID
- `timestamp`: UTC 时间戳

## `GET /session/{user_id}`

返回用户会话状态，包括：

- `session_id`
- `turn_count`
- `last_seen_at`

## `GET /llm/models`

返回当前模型配置与连通性信息，包括：

- `provider`
- `base_url`
- `configured_model`
- `api_key_configured`
- `models`
