import logging
import time
import json
import re
from dataclasses import dataclass

from openai import OpenAI

from app.core.config import settings
from app.core.markdown_assets import read_required_markdown_asset
from app.services.prompt_composer import PromptContext

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RetrievalPlan:
    should_retrieve: bool
    queries: list[str]
    reason: str = ""


class LLMClient:
    def __init__(self) -> None:
        self._client: OpenAI | None = None
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0

    @staticmethod
    def _render_template(template: str, values: dict[str, object]) -> str:
        return template.format(**values)

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

    def plan_retrieval(
        self,
        *,
        user_message: str,
        retrieval_report: str,
        remaining_retrievals: int,
    ) -> RetrievalPlan:
        provider = settings.llm.provider.lower().strip()
        if provider != "kilo" or not settings.llm.api_key:
            return RetrievalPlan(should_retrieve=True, queries=[user_message], reason="fallback:provider")
        if self._is_circuit_open():
            return RetrievalPlan(should_retrieve=True, queries=[user_message], reason="fallback:circuit_open")

        planner_system = read_required_markdown_asset("prompt/retrieval_planner_system.md")
        planner_user_template = read_required_markdown_asset("prompt/retrieval_planner_user.md")
        planner_user = self._render_template(
            planner_user_template,
            {
                "user_message": user_message,
                "retrieval_report": retrieval_report,
                "remaining_retrievals": remaining_retrievals,
            },
        )
        client = self._get_client()
        try:
            response = client.chat.completions.create(
                model=settings.llm.model,
                temperature=0,
                messages=[
                    {"role": "system", "content": planner_system},
                    {"role": "user", "content": planner_user},
                ],
            )
            content = response.choices[0].message.content if response.choices else ""
            self._on_request_success()
            parsed = self._parse_retrieval_plan(content or "", user_message=user_message)
            logger.debug(
                "Retrieval plan generated | should_retrieve=%s | queries=%s | reason=%s",
                parsed.should_retrieve,
                parsed.queries,
                parsed.reason,
            )
            return parsed
        except Exception as exc:
            self._on_request_failure(exc)
            logger.warning("Retrieval plan generation failed, fallback to default query: %s", exc)
            return RetrievalPlan(should_retrieve=True, queries=[user_message], reason="fallback:error")

    @staticmethod
    def _parse_retrieval_plan(raw: str, *, user_message: str) -> RetrievalPlan:
        stripped = raw.strip()
        if not stripped:
            return RetrievalPlan(should_retrieve=True, queries=[user_message], reason="fallback:empty")
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", stripped, flags=re.S)
            if not match:
                return RetrievalPlan(should_retrieve=True, queries=[user_message], reason="fallback:invalid_json")
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                return RetrievalPlan(should_retrieve=True, queries=[user_message], reason="fallback:invalid_json")

        should_retrieve = bool(data.get("should_retrieve", True))
        raw_queries = data.get("queries", [])
        if not isinstance(raw_queries, list):
            raw_queries = []
        queries = [str(item).strip() for item in raw_queries if str(item).strip()][:3]
        if should_retrieve and not queries:
            queries = [user_message]
        reason = str(data.get("reason", "")).strip()
        return RetrievalPlan(should_retrieve=should_retrieve, queries=queries, reason=reason)

    @staticmethod
    def _service_unavailable_message() -> str:
        admin_id = str(settings.onebot.debug_only_user_id).strip()
        return f"当前不可用，请联系管理员（debug账号：{admin_id}）"

    @staticmethod
    def _build_system_prompt(prompt_context: PromptContext) -> str:
        wrapper_template = read_required_markdown_asset("prompt/system_wrapper.md")
        return wrapper_template.format(
            system_core=prompt_context.system_core,
            system_runtime=prompt_context.system_runtime,
            profile_context=prompt_context.profile_context,
            memory_context=prompt_context.memory_context,
            policy_notice=prompt_context.policy_notice,
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
