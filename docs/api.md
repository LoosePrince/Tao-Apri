# API 概览

## `GET /health`

返回服务健康状态。

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

## `GET /session/{user_id}`

返回用户会话状态，包括：

- `session_id`
- `turn_count`
- `last_seen_at`
