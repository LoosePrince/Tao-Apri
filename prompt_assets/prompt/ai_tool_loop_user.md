用户消息：
{user_message}

当前本地时间（ISO）：
{current_local_time}

当前本地日期：
{current_local_date}

当前本地时区：
{current_local_timezone}

当前 UTC 时间（ISO）：
{current_utc_time}

时间输入规则（用于 schedule_delayed_task）：
- `time` 仅允许两种格式：
  - 相对时间示例：`2h`、`30m`、`45s`、`1d`
  - 绝对时间示例：`2026.4.18 17:23:59`
- 绝对时间按系统配置时区解释，不允许指定其他时区。

可用工具：
{tool_specs_json}

已有工具结果：
{tool_results_json}

请按系统要求输出 JSON。
