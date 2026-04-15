请判断以下用户输入是否需要检索历史记忆再回答。
要求：
1) 若用户在追问上下文、身份、偏好、过往事件，should_retrieve=true。
2) queries 为适合语义检索的短语，最多 3 条。
3) 若不需要检索，queries 返回空数组。
4) 你可以基于“上一轮检索反馈”决定是否继续检索；若信息已足够，返回 should_retrieve=false。
5) 你最多还能检索 {remaining_retrievals} 次。
上一轮检索反馈：
{retrieval_report}
用户输入：{user_message}
