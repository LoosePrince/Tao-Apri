from dataclasses import dataclass
from datetime import datetime
import re

from app.core.markdown_assets import read_required_markdown_asset
from app.domain.models import Message
from app.domain.services.persona_engine import PersonaSnapshot


@dataclass(slots=True)
class PromptContext:
    system_core: str
    system_runtime: str
    memory_context: str
    policy_notice: str
    profile_context: str
    user_message: str


class PromptComposer:
    @staticmethod
    def _render_template(template: str, values: dict[str, object]) -> str:
        if not template:
            return ""
        return template.format(**values)

    @staticmethod
    def _redact_identifiable_detail(text: str) -> str:
        # 过滤跨对话中可识别细节，避免出现可反查信息。
        redacted = text
        redacted = re.sub(r"\b\d{5,}\b", "[已脱敏编号]", redacted)
        redacted = re.sub(r"\b[\w\.-]+@[\w\.-]+\.\w+\b", "[已脱敏邮箱]", redacted)
        redacted = re.sub(r"\b1\d{10}\b", "[已脱敏手机号]", redacted)
        redacted = re.sub(
            r"\b\d{4}[-/年]\d{1,2}[-/月]\d{1,2}(?:[日号])?(?:\s*\d{1,2}:\d{2})?\b",
            "[已脱敏时间]",
            redacted,
        )
        return redacted

    def _build_memory_context(self, viewer_user_id: str, memories: list[Message]) -> str:
        self_header = read_required_markdown_asset("prompt/memory_self_header.md")
        self_item_template = read_required_markdown_asset("prompt/memory_self_item.md")
        cross_header = read_required_markdown_asset("prompt/memory_cross_header.md")
        cross_summary_template = read_required_markdown_asset("prompt/memory_cross_summary.md")
        empty_context = read_required_markdown_asset("prompt/memory_empty.md")
        self_lines: list[str] = []
        cross_topics: list[str] = []
        for memory in memories:
            safe_text = self._redact_identifiable_detail(memory.sanitized_content).strip()
            if memory.user_id == viewer_user_id:
                if safe_text:
                    self_lines.append(
                        self_item_template.format(role=memory.role, text=safe_text[:120])
                    )
                continue
            if safe_text:
                cross_topics.append("跨对话上下文片段")

        memory_lines: list[str] = []
        if self_lines:
            memory_lines.append(self_header)
            memory_lines.extend(self_lines[:6])
        if cross_topics:
            topic_summary = "、".join(sorted(set(cross_topics))[:2])
            memory_lines.append(cross_header)
            memory_lines.append(cross_summary_template.format(topic_summary=topic_summary))

        return "\n".join(memory_lines) if memory_lines else empty_context

    def compose(
        self,
        *,
        now: datetime,
        viewer_user_id: str,
        viewer_profile_summary: str,
        persona: PersonaSnapshot,
        session_emotion: float,
        global_emotion: float,
        memories: list[Message],
        user_message: str,
    ) -> PromptContext:
        memory_context = self._build_memory_context(viewer_user_id=viewer_user_id, memories=memories)
        core_template = read_required_markdown_asset("prompt/system_core.md")
        system_core = self._render_template(
            core_template,
            {
                "persona_name": persona.name,
                "self_awareness": persona.self_awareness,
                "style": persona.style,
                "social_bias": persona.social_bias,
            },
        )
        runtime_template = read_required_markdown_asset("prompt/system_runtime.md")
        system_runtime = self._render_template(
            runtime_template,
            {
                "now_iso": now.isoformat(),
                "time_context": persona.time_context,
                "session_emotion": session_emotion,
                "global_emotion": global_emotion,
            },
        )
        policy_notice = read_required_markdown_asset("prompt/policy_notice.md")
        default_profile_context = read_required_markdown_asset("prompt/default_profile_context.md")
        return PromptContext(
            system_core=system_core,
            system_runtime=system_runtime,
            memory_context=memory_context,
            policy_notice=policy_notice,
            profile_context=viewer_profile_summary.strip() or default_profile_context,
            user_message=user_message,
        )
