你将获得一组输入特征，用于判断是否需要对用户发送 assistant 消息。

请输出 JSON：
{"should_reply": true|false, "reason": "<简短原因>"}

输入特征：
- user_message:
{user_message}
- session_emotion: {session_emotion}
- global_emotion: {global_emotion}
- fatigue_level: {fatigue_level}
- emotion_peak_level: {emotion_peak_level}
- memory_count: {memory_count}
- current_hour: {current_hour}
- current_date: {current_date}
- current_year: {current_year}
- scene_type: {scene_type}
- group_bot_mentioned: {group_bot_mentioned}
- group_allow_autonomous: {group_allow_autonomous}

重要：
- 当 scene_type 为 group 时，默认倾向 **不回复**；只有在你能明确判断“需要接话”时才回复。
- 当 scene_type 为 private 时，在不确定时可倾向回复。
