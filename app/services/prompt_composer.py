from dataclasses import dataclass
from datetime import datetime
import re

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
    _TOPIC_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("学习与考试", ("学习", "考试", "作业", "上课", "复习", "成绩")),
        ("工作与职业", ("工作", "加班", "同事", "项目", "面试", "公司")),
        ("作息与健康", ("睡", "失眠", "作息", "疲惫", "生病", "运动", "饮食")),
        ("情绪与关系", ("开心", "难过", "焦虑", "压力", "朋友", "家人", "恋爱")),
        ("娱乐与兴趣", ("游戏", "动漫", "电影", "音乐", "读书", "旅行")),
    )

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

    def _infer_topic(self, text: str) -> str:
        lowered = text.lower()
        for label, keywords in self._TOPIC_RULES:
            if any(keyword in text or keyword in lowered for keyword in keywords):
                return label
        return "日常近况"

    def _build_memory_context(self, viewer_user_id: str, memories: list[Message]) -> str:
        self_lines: list[str] = []
        cross_topics: list[str] = []
        for memory in memories:
            safe_text = self._redact_identifiable_detail(memory.sanitized_content).strip()
            if memory.user_id == viewer_user_id:
                if safe_text:
                    self_lines.append(f"- (self/{memory.role}) {safe_text[:120]}")
                continue
            cross_topics.append(self._infer_topic(safe_text))

        memory_lines: list[str] = []
        if self_lines:
            memory_lines.append("### 当前用户相关记忆")
            memory_lines.extend(self_lines[:6])
        if cross_topics:
            topic_summary = "、".join(sorted(set(cross_topics))[:4])
            memory_lines.append("### 跨对话模糊参考")
            memory_lines.append(
                f"- 其他对话大概涉及：{topic_summary}。仅可用于把握整体语境，禁止输出任何可识别细节或原句。"
            )

        return "\n".join(memory_lines) if memory_lines else "- 暂无相关记忆"

    def compose(
        self,
        *,
        now: datetime,
        viewer_user_id: str,
        persona: PersonaSnapshot,
        session_emotion: float,
        global_emotion: float,
        memories: list[Message],
        user_message: str,
    ) -> PromptContext:
        memory_context = self._build_memory_context(viewer_user_id=viewer_user_id, memories=memories)
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
