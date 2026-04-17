from datetime import datetime, timezone

from app.domain.models import Message
from app.domain.services.persona_engine import PersonaSnapshot
from app.services.prompt_composer import PromptComposer


def _message(*, user_id: str, role: str, text: str) -> Message:
    return Message(
        message_id=f"msg-{user_id}-{role}",
        user_id=user_id,
        role=role,
        raw_content=text,
        sanitized_content=text,
        created_at=datetime.now(timezone.utc),
        session_id=f"s-{user_id}",
        emotion_score=0.0,
        related_user_ids=[],
    )


def test_cross_conversation_memories_are_topic_level_only() -> None:
    composer = PromptComposer()
    persona = PersonaSnapshot(
        name="LinXi",
        self_awareness="你知道自己是AI。",
        style="自然聊天。",
        social_bias="喜欢社交。",
        time_context="日间模式。",
    )
    memories = [
        _message(user_id="u_self", role="user", text="我最近学习压力有点大"),
        _message(
            user_id="u_other",
            role="user",
            text="张三手机号是13800138000，今晚20:30在海淀见面聊加班项目",
        ),
    ]

    ctx = composer.compose(
        now=datetime.now(timezone.utc),
        viewer_user_id="u_self",
        viewer_profile_summary="偏好简洁交流",
        persona=persona,
        session_emotion=0.1,
        global_emotion=0.2,
        memories=memories,
        user_message="最近大家都在聊什么",
    )

    assert "### 跨对话模糊参考" in ctx.memory_context
    assert "工作与职业" in ctx.memory_context
    assert "13800138000" not in ctx.memory_context
    assert "20:30" not in ctx.memory_context
    assert "张三" not in ctx.memory_context
    assert "参数执行总则" in ctx.parameter_context
    assert "### LLM__TEMPERATURE" in ctx.parameter_context
    assert "示例（用户）" in ctx.parameter_context
