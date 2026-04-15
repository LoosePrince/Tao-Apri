from dataclasses import dataclass
from datetime import datetime

from app.core.markdown_assets import read_markdown_asset
from app.domain.models import Message
from app.domain.services.persona_engine import PersonaSnapshot


@dataclass(slots=True)
class PromptContext:
    system_core: str
    system_runtime: str
    memory_context: str
    policy_notice: str
    user_message: str


class PromptComposer:
    @staticmethod
    def _render_template(template: str, values: dict[str, object]) -> str:
        if not template:
            return ""
        return template.format(**values)

    def compose(
        self,
        *,
        now: datetime,
        persona: PersonaSnapshot,
        session_emotion: float,
        global_emotion: float,
        memories: list[Message],
        user_message: str,
    ) -> PromptContext:
        memory_lines = [
            f"- ({m.role}) {m.sanitized_content}"
            for m in memories
        ]
        memory_context = "\n".join(memory_lines) if memory_lines else "- 暂无相关记忆"
        core_template = read_markdown_asset("prompt/system_core.md")
        system_core = self._render_template(
            core_template,
            {
                "persona_name": persona.name,
                "self_awareness": persona.self_awareness,
                "style": persona.style,
                "social_bias": persona.social_bias,
            },
        )
        if not system_core:
            system_core = (
                f"你是 {persona.name}。{persona.self_awareness} "
                f"{persona.style} {persona.social_bias}"
            )
        runtime_template = read_markdown_asset("prompt/system_runtime.md")
        system_runtime = self._render_template(
            runtime_template,
            {
                "now_iso": now.isoformat(),
                "time_context": persona.time_context,
                "session_emotion": session_emotion,
                "global_emotion": global_emotion,
            },
        )
        if not system_runtime:
            system_runtime = (
                f"当前时间: {now.isoformat()}。{persona.time_context} "
                f"会话情绪={session_emotion:.2f}，全局情绪偏移={global_emotion:.2f}。"
            )
        policy_notice = read_markdown_asset("prompt/policy_notice.md")
        if not policy_notice:
            policy_notice = (
                "提示用户：这是非私密AI，不建议输入敏感个人信息。"
                "可引用他人信息时必须使用模糊摘要，不给出可识别细节。"
            )
        return PromptContext(
            system_core=system_core,
            system_runtime=system_runtime,
            memory_context=memory_context,
            policy_notice=policy_notice,
            user_message=user_message,
        )
