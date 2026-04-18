请基于以下统一上下文完成单次决策。

当前用户消息：
{user_message}

完整系统上下文（与主回复同源）：
{unified_system_context}

当前关系状态：
{relation_json}

关系规则边界（与上文关系状态一起综合判断；语气需符合边界）：
{relation_boundary_context}

当前画像状态：
{profile_json}

当前会话状态：
- scene_type={scene_type}
- group_bot_mentioned={group_bot_mentioned}
- group_allow_autonomous={group_allow_autonomous}
- session_emotion={session_emotion}
- global_emotion={global_emotion}
- memory_count={memory_count}
- current_hour={current_hour}
- current_date={current_date}
- current_year={current_year}

图像识别补充上下文（辅助证据，可能不完整）：
{image_context}

本回合工具执行摘要（已压缩，含输入/输出有效信息；无则为占位）：
{execution_digest}

当前可用工具名称列表（只读核对；实际是否调用以摘要为准）：
{available_tools_summary}

输出要求：
- 只输出 JSON。
- `should_reply=false` 时 `reply` 必须为空。
- `preferred_address` 最长 12 字符。
- 数值字段都必须在 0 到 1 之间。
