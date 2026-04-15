import logging
import time

from openai import OpenAI

from app.core.config import settings
from app.services.prompt_composer import PromptContext

logger = logging.getLogger(__name__)


class LLMClient:
    def __init__(self) -> None:
        self._client: OpenAI | None = None
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0

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
        if self._is_circuit_open():
            logger.warning("Skip list models because circuit is open.")
            return []
        client = self._get_client()
        try:
            logger.info("Listing models via provider=%s", provider)
            models = client.models.list()
            self._on_request_success()
            return sorted({item.id for item in models.data if getattr(item, "id", None)})
        except Exception as exc:
            logger.warning("Kilo list models failed: %s", exc)
            self._on_request_failure(exc)
            return []

    def startup_health_check(self) -> bool:
        provider = settings.llm.provider.lower().strip()
        if provider != "kilo":
            logger.info("Skip LLM startup health check because provider=%s", provider)
            return True
        if not settings.llm.api_key:
            logger.error("LLM startup health check failed: api_key is empty.")
            return False
        models = self.list_available_models()
        if models:
            logger.info("LLM startup health check passed | models=%s", len(models))
            return True
        logger.error("LLM startup health check failed: unable to list models.")
        return False

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
                "当前用户画像：\n" + prompt_context.profile_context,
                "记忆上下文：\n" + prompt_context.memory_context,
                "策略说明：\n" + prompt_context.policy_notice,
            ]
        ).strip()

    def _call_kilo(self, prompt_context: PromptContext) -> str:
        if not settings.llm.api_key:
            logger.error("LLM provider is kilo but api_key is empty.")
            return ""
        if self._is_circuit_open():
            logger.error("Kilo request blocked by circuit breaker.")
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
        max_attempts = settings.llm.retry_max_attempts
        backoff = settings.llm.retry_backoff_seconds
        last_exc: Exception | None = None

        for attempt in range(1, max_attempts + 1):
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
                self._on_request_success()
                logger.info("Kilo response received | content_len=%s | attempt=%s", len(content or ""), attempt)
                return (content or "").strip()
            except Exception as exc:
                last_exc = exc
                self._on_request_failure(exc)
                logger.error("Kilo request failed | attempt=%s/%s | err=%s", attempt, max_attempts, exc)
                if attempt < max_attempts and not self._is_circuit_open():
                    sleep_seconds = backoff * attempt
                    if sleep_seconds > 0:
                        time.sleep(sleep_seconds)
                else:
                    break

        if last_exc:
            logger.error("Kilo request exhausted retries: %s", last_exc)
        return ""

    def _get_client(self) -> OpenAI:
        if self._client is None:
            self._client = OpenAI(
                api_key=settings.llm.api_key,
                base_url=settings.llm.base_url,
                timeout=settings.llm.timeout_seconds,
            )
        return self._client

    def _is_circuit_open(self) -> bool:
        return time.monotonic() < self._circuit_open_until

    def _on_request_success(self) -> None:
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0

    def _on_request_failure(self, exc: Exception) -> None:
        self._consecutive_failures += 1
        threshold = settings.llm.circuit_breaker_failure_threshold
        if self._consecutive_failures >= threshold:
            open_seconds = settings.llm.circuit_breaker_open_seconds
            self._circuit_open_until = time.monotonic() + open_seconds
            logger.error(
                "Circuit breaker opened | failures=%s | open_seconds=%s | reason=%s",
                self._consecutive_failures,
                open_seconds,
                exc,
            )
