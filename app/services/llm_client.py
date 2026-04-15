import logging

from openai import OpenAI

from app.core.config import settings
from app.services.prompt_composer import PromptContext

logger = logging.getLogger(__name__)


class LLMClient:
    def __init__(self) -> None:
        self._client: OpenAI | None = None

    def generate_reply(
        self,
        *,
        prompt_context: PromptContext,
        session_emotion: float,
        global_emotion: float,
        memory_count: int,
        include_notice: bool,
    ) -> str:
        provider = settings.llm.provider.lower().strip()
        if provider == "kilo":
            reply = self._call_kilo(prompt_context)
            if reply:
                return reply
        return self._mock_reply(
            prompt_ctx_text=prompt_context.user_message,
            memory_count=memory_count,
            session_emotion=session_emotion,
            global_emotion=global_emotion,
            include_notice=include_notice,
        )

    def list_available_models(self) -> list[str]:
        provider = settings.llm.provider.lower().strip()
        if provider != "kilo":
            return []
        if not settings.llm.api_key:
            return []
        client = self._get_client()
        try:
            logger.info("Listing models via provider=%s", provider)
            models = client.models.list()
            return sorted({item.id for item in models.data if getattr(item, "id", None)})
        except Exception as exc:
            logger.warning("Kilo list models failed: %s", exc)
            return []

    @staticmethod
    def _mock_reply(
        *,
        prompt_ctx_text: str,
        memory_count: int,
        session_emotion: float,
        global_emotion: float,
        include_notice: bool,
    ) -> str:
        emotion_hint = "平静"
        if session_emotion > 0.35:
            emotion_hint = "偏开心"
        elif session_emotion < -0.35:
            emotion_hint = "偏低落"
        body = (
            f"{prompt_ctx_text}。"
            f"我现在是{emotion_hint}状态（会话{session_emotion:.2f}/全局{global_emotion:.2f}）。"
            f"我参考了{memory_count}条相关记忆，并保持对他人信息的模糊表达。"
        )
        if include_notice:
            return "提醒：这是非私密AI，请避免发送敏感个人信息。" + body
        return body

    @staticmethod
    def _build_system_prompt(prompt_context: PromptContext) -> str:
        return "\n\n".join(
            [
                prompt_context.system_core,
                prompt_context.system_runtime,
                "记忆上下文：\n" + prompt_context.memory_context,
                "策略说明：\n" + prompt_context.policy_notice,
            ]
        ).strip()

    def _call_kilo(self, prompt_context: PromptContext) -> str:
        if not settings.llm.api_key:
            logger.warning("LLM provider is kilo but api_key is empty, fallback to mock.")
            return ""
        client = self._get_client()
        logger.info(
            "Calling kilo chat completion | model=%s | base_url=%s",
            settings.llm.model,
            settings.llm.base_url,
        )
        logger.debug(
            "Kilo request payload summary | system_len=%s | user_len=%s | temperature=%.2f",
            len(self._build_system_prompt(prompt_context)),
            len(prompt_context.user_message),
            settings.llm.temperature,
        )
        try:
            response = client.chat.completions.create(
                model=settings.llm.model,
                temperature=settings.llm.temperature,
                messages=[
                    {"role": "system", "content": self._build_system_prompt(prompt_context)},
                    {"role": "user", "content": prompt_context.user_message},
                ],
            )
            content = response.choices[0].message.content if response.choices else ""
            logger.info("Kilo response received | content_len=%s", len(content or ""))
            return (content or "").strip()
        except Exception as exc:
            logger.warning("Kilo request failed, fallback to mock: %s", exc)
            return ""

    def _get_client(self) -> OpenAI:
        if self._client is None:
            self._client = OpenAI(
                api_key=settings.llm.api_key,
                base_url=settings.llm.base_url,
                timeout=settings.llm.timeout_seconds,
            )
        return self._client
