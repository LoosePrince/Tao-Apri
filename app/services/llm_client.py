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
        del session_emotion, global_emotion, memory_count, include_notice
        provider = settings.llm.provider.lower().strip()
        if provider == "kilo":
            reply = self._call_kilo(prompt_context)
            if reply:
                return reply
            return self._service_unavailable_message()
        logger.warning("Unknown LLM provider '%s', return unavailable notice.", provider)
        return self._service_unavailable_message()

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
    def _service_unavailable_message() -> str:
        admin_id = str(settings.onebot.debug_only_user_id).strip()
        return f"当前不可用，请联系管理员（debug账号：{admin_id}）"

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
            logger.error("LLM provider is kilo but api_key is empty.")
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
            logger.error("Kilo request failed: %s", exc)
            return ""

    def _get_client(self) -> OpenAI:
        if self._client is None:
            self._client = OpenAI(
                api_key=settings.llm.api_key,
                base_url=settings.llm.base_url,
                timeout=settings.llm.timeout_seconds,
            )
        return self._client
