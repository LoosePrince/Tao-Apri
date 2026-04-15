请基于以下信息生成画像：
- 当前小时：{current_hour}
- 当前日期：{current_date}
- 当前年份：{current_year}
- 会话情绪：{session_emotion}
- 全局情绪：{global_emotion}
- 历史用户消息（按时间序）：
{user_texts}

严格时间规则：
- 用户提到“上一年/去年”时，必须按“当前年份-1”推断，不得臆测其他年份。
- 若无法确定具体年份，写“未明确”，禁止编造精确年份。
