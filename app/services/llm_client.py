import logging
import time
import json
import re
import threading
import base64
from dataclasses import dataclass

from openai import OpenAI

from app.core.config import settings
from app.core.markdown_assets import read_required_markdown_asset
from app.core.rule_lexicons import allowed_topic_labels
from app.services.prompt_composer import PromptContext

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RetrievalPlan:
    should_retrieve: bool
    queries: list[str]
    reason: str = ""


@dataclass(slots=True)
class GroupEmotionDecision:
    score: float
    text: str


@dataclass(slots=True)
class CrossAccessDecision:
    allowed_message_ids: set[str]
    relation_denied: int
    similarity_denied: int
    preference_denied: int


@dataclass(slots=True)
class ShouldReplyDecision:
    should_reply: bool
    reason: str = ""


@dataclass(slots=True)
class UnifiedDecision:
    should_reply: bool
    skip_reason: str
    reply: str
    profile_update: dict[str, object]
    relation_update: dict[str, object]
    retrieval_plan: RetrievalPlan


class LLMClient:
    def __init__(self) -> None:
        self._client: OpenAI | None = None
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0
        self._circuit_lock = threading.RLock()

    @staticmethod
    def _render_template(template: str, values: dict[str, object]) -> str:
        # Use a safe placeholder renderer:
        # - Replace only "{identifier}" placeholders.
        # - Leave JSON examples like {"should_reply": true|false} intact (do NOT treat them as placeholders).
        def _replace(match: re.Match[str]) -> str:
            key = match.group(1)
            if key in values:
                return str(values[key])
            return match.group(0)

        return re.sub(r"\{([a-zA-Z_]\w*)\}", _replace, template)

    @staticmethod
    def _extract_json(raw: str) -> dict[str, object]:
        stripped = (raw or "").strip()
        if not stripped:
            return {}
        # First, try full-string parse (decider is expected to output JSON only).
        try:
            parsed = json.loads(stripped)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            pass

        # Fallback: find the first balanced JSON object.
        # This avoids the previous greedy `{.*}` extraction that could swallow multiple objects.
        start_idx = stripped.find("{")
        while start_idx != -1:
            depth = 0
            in_string = False
            escape = False
            for i in range(start_idx, len(stripped)):
                ch = stripped[i]
                if escape:
                    escape = False
                    continue
                if ch == "\\":
                    escape = True
                    continue
                if ch == '"':
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        candidate = stripped[start_idx : i + 1]
                        try:
                            parsed = json.loads(candidate)
                            return parsed if isinstance(parsed, dict) else {}
                        except json.JSONDecodeError:
                            break
            start_idx = stripped.find("{", start_idx + 1)

        return {}

    @staticmethod
    def _coerce_bool(value: object, default: bool = True) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "y", "on"}:
                return True
            if normalized in {"false", "0", "no", "n", "off"}:
                return False
        return default

    def _call_json_decider(self, *, system_asset: str, user_asset: str, values: dict[str, object]) -> dict[str, object]:
        provider = settings.llm.provider.lower().strip()
        if provider != "kilo" or not settings.llm.api_key or self._is_circuit_open():
            return {}
        system_prompt = read_required_markdown_asset(system_asset)
        user_template = read_required_markdown_asset(user_asset)
        user_prompt = self._render_template(user_template, values)
        client = self._get_client()
        try:
            response = client.chat.completions.create(
                model=settings.llm.model,
                temperature=0,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            content = response.choices[0].message.content if response.choices else ""
            self._on_request_success()
            return self._extract_json(content or "")
        except Exception as exc:
            self._on_request_failure(exc)
            logger.warning("JSON decider call failed | system_asset=%s | err=%s", system_asset, exc)
            return {}

    def classify_topic(self, text: str) -> str:
        data = self._call_json_decider(
            system_asset="prompt/ai_topic_system.md",
            user_asset="prompt/ai_topic_user.md",
            values={"text": text},
        )
        topic = str(data.get("topic", "")).strip()
        allowed = allowed_topic_labels()
        return topic if topic in allowed else "日常近况"

    def generate_profile_decision(
        self,
        *,
        user_texts: list[str],
        current_hour: int,
        current_date: str,
        current_year: int,
        session_emotion: float,
        global_emotion: float,
    ) -> dict[str, object]:
        return self._call_json_decider(
            system_asset="prompt/ai_profile_system.md",
            user_asset="prompt/ai_profile_user.md",
            values={
                "current_hour": current_hour,
                "current_date": current_date,
                "current_year": current_year,
                "session_emotion": f"{session_emotion:.4f}",
                "global_emotion": f"{global_emotion:.4f}",
                "user_texts": "\n".join(f"- {item}" for item in user_texts) or "- 无",
            },
        )

    def evolve_relation_decision(self, *, relation_json: str, user_message: str, reply: str) -> dict[str, object]:
        return self._call_json_decider(
            system_asset="prompt/ai_relation_system.md",
            user_asset="prompt/ai_relation_user.md",
            values={"relation_json": relation_json, "user_message": user_message, "reply": reply},
        )

    def summarize_group_emotion(self, *, scores: list[float]) -> GroupEmotionDecision:
        data = self._call_json_decider(
            system_asset="prompt/ai_group_emotion_system.md",
            user_asset="prompt/ai_group_emotion_user.md",
            values={"scores_json": json.dumps(scores, ensure_ascii=False)},
        )
        score = float(data.get("group_emotion_avg", 0.0) or 0.0)
        score = max(-1.0, min(1.0, score))
        text = str(data.get("group_emotion_text", "")).strip() or "群体情绪：中性平稳。"
        return GroupEmotionDecision(score=score, text=text)

    def decide_cross_access(
        self,
        *,
        viewer_user_id: str,
        query: str,
        memories: list[dict[str, object]],
    ) -> CrossAccessDecision:
        data = self._call_json_decider(
            system_asset="prompt/ai_cross_access_system.md",
            user_asset="prompt/ai_cross_access_user.md",
            values={
                "viewer_user_id": viewer_user_id,
                "query": query,
                "memories_json": json.dumps(memories, ensure_ascii=False),
            },
        )
        raw_ids = data.get("allowed_message_ids", [])
        allowed_ids = {
            str(item).strip()
            for item in (raw_ids if isinstance(raw_ids, list) else [])
            if str(item).strip()
        }
        return CrossAccessDecision(
            allowed_message_ids=allowed_ids,
            relation_denied=int(data.get("relation_denied", 0) or 0),
            similarity_denied=int(data.get("similarity_denied", 0) or 0),
            preference_denied=int(data.get("preference_denied", 0) or 0),
        )

    def decide_should_reply(
        self,
        *,
        user_message: str,
        session_emotion: float,
        global_emotion: float,
        fatigue_level: float,
        emotion_peak_level: float,
        memory_count: int,
        current_hour: int,
        current_date: str,
        current_year: int,
        scene_type: str = "private",
        group_bot_mentioned: bool = False,
        group_allow_autonomous: bool = False,
    ) -> ShouldReplyDecision:
        data = self._call_json_decider(
            system_asset="prompt/ai_should_reply_system.md",
            user_asset="prompt/ai_should_reply_user.md",
            values={
                "user_message": user_message,
                "session_emotion": f"{session_emotion:.4f}",
                "global_emotion": f"{global_emotion:.4f}",
                "fatigue_level": f"{fatigue_level:.4f}",
                "emotion_peak_level": f"{emotion_peak_level:.4f}",
                "memory_count": str(memory_count),
                "current_hour": str(current_hour),
                "current_date": current_date,
                "current_year": str(current_year),
                "scene_type": scene_type,
                "group_bot_mentioned": str(group_bot_mentioned).lower(),
                "group_allow_autonomous": str(group_allow_autonomous).lower(),
            },
        )

        raw_should_reply = data.get("should_reply", True) if isinstance(data, dict) else True
        should_reply = self._coerce_bool(raw_should_reply, default=True)

        reason = str(data.get("reason", "")).strip() if isinstance(data, dict) else ""
        if not reason:
            reason = "fallback:default_true"

        return ShouldReplyDecision(should_reply=should_reply, reason=reason)

    def extract_keywords(self, *, text: str, top_k: int = 5) -> list[str]:
        data = self._call_json_decider(
            system_asset="prompt/ai_keyword_extract_system.md",
            user_asset="prompt/ai_keyword_extract_user.md",
            values={"text": text, "top_k": top_k},
        )
        raw = data.get("keywords", [])
        keywords = [str(item).strip() for item in (raw if isinstance(raw, list) else []) if str(item).strip()]
        return keywords[:max(1, min(10, top_k))]

    def summarize_long_message(self, *, text: str) -> str:
        data = self._call_json_decider(
            system_asset="prompt/ai_long_summary_system.md",
            user_asset="prompt/ai_long_summary_user.md",
            values={"text": text},
        )
        brief = str(data.get("summary", "")).strip()
        if brief:
            return brief
        return (text[:80] + "...") if len(text) > 80 else text

    def summarize_window_messages(self, *, messages: list[str]) -> str:
        data = self._call_json_decider(
            system_asset="prompt/ai_window_summary_system.md",
            user_asset="prompt/ai_window_summary_user.md",
            values={"messages": "\n".join(f"- {item}" for item in messages)},
        )
        summary = str(data.get("summary", "")).strip()
        if summary:
            return summary
        return "\n".join(f"- {item}" for item in messages[:8])

    def analyze_image_with_vision_model(
        self,
        *,
        image_url: str | None = None,
        image_bytes: bytes | None = None,
        prompt: str = "请简要描述图片内容，并提取可见文字。",
        mime_type: str = "image/png",
    ) -> str:
        if not settings.vision.enabled:
            return ""
        if not settings.vision.api_key or not settings.vision.base_url or not settings.vision.model:
            return ""
        content_parts: list[dict[str, object]] = [{"type": "text", "text": prompt}]
        if image_url:
            content_parts.append({"type": "image_url", "image_url": {"url": image_url}})
        elif image_bytes:
            b64 = base64.b64encode(image_bytes).decode("ascii")
            content_parts.append({"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64}"}})
        else:
            return ""
        try:
            vision_client = OpenAI(
                api_key=settings.vision.api_key,
                base_url=settings.vision.base_url,
                timeout=settings.vision.download_timeout_seconds,
            )
            response = vision_client.chat.completions.create(
                model=settings.vision.model,
                temperature=0,
                messages=[{"role": "user", "content": content_parts}],
            )
            text = response.choices[0].message.content if response.choices else ""
            return (text or "").strip()
        except Exception as exc:
            logger.warning("Vision model analyze failed | err=%s", exc)
            return ""

    def generate_unified_decision(
        self,
        *,
        prompt_context: PromptContext,
        user_message: str,
        relation_json: str,
        profile_json: str,
        session_emotion: float,
        global_emotion: float,
        memory_count: int,
        current_hour: int,
        current_date: str,
        current_year: int,
        scene_type: str = "private",
        group_bot_mentioned: bool = False,
        group_allow_autonomous: bool = False,
        include_notice: bool = True,
        image_context: str = "",
    ) -> UnifiedDecision:
        provider = settings.llm.provider.lower().strip()
        if provider != "kilo" or not settings.llm.api_key or self._is_circuit_open():
            return UnifiedDecision(
                should_reply=True,
                skip_reason="fallback:provider_or_circuit",
                reply="",
                profile_update={},
                relation_update={},
                retrieval_plan=RetrievalPlan(should_retrieve=True, queries=[user_message], reason="fallback"),
            )

        system_prompt = read_required_markdown_asset("prompt/ai_unified_decision_system.md")
        user_template = read_required_markdown_asset("prompt/ai_unified_decision_user.md")
        unified_system_context = self._build_system_prompt(prompt_context, include_notice=include_notice)
        user_prompt = self._render_template(
            user_template,
            {
                "user_message": user_message,
                "unified_system_context": unified_system_context,
                "relation_json": relation_json,
                "profile_json": profile_json,
                "scene_type": scene_type,
                "group_bot_mentioned": str(group_bot_mentioned).lower(),
                "group_allow_autonomous": str(group_allow_autonomous).lower(),
                "session_emotion": f"{session_emotion:.4f}",
                "global_emotion": f"{global_emotion:.4f}",
                "memory_count": str(memory_count),
                "current_hour": str(current_hour),
                "current_date": current_date,
                "current_year": str(current_year),
                "image_context": image_context.strip() or "无",
            },
        )
        client = self._get_client()
        try:
            response = client.chat.completions.create(
                model=settings.llm.model,
                temperature=0,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            content = response.choices[0].message.content if response.choices else ""
            self._on_request_success()
            data = self._extract_json(content or "")
        except Exception as exc:
            self._on_request_failure(exc)
            logger.warning("Unified decision call failed | err=%s", exc)
            data = {}

        should_reply = self._coerce_bool(data.get("should_reply", True), default=True)
        skip_reason = str(data.get("skip_reason", "")).strip()
        reply = str(data.get("reply", "")).strip()
        if not should_reply:
            reply = ""
            if not skip_reason:
                skip_reason = "skip:unified_should_reply_false"
        profile_update = data.get("profile_update", {})
        if not isinstance(profile_update, dict):
            profile_update = {}
        relation_update = data.get("relation_update", {})
        if not isinstance(relation_update, dict):
            relation_update = {}
        retrieval_data = data.get("retrieval_plan", {})
        if not isinstance(retrieval_data, dict):
            retrieval_data = {}
        raw_queries = retrieval_data.get("queries", [])
        queries = [str(item).strip() for item in (raw_queries if isinstance(raw_queries, list) else []) if str(item).strip()][:3]
        if not queries:
            queries = [user_message]
        retrieval_plan = RetrievalPlan(
            should_retrieve=self._coerce_bool(retrieval_data.get("should_retrieve", True), default=True),
            queries=queries,
            reason=str(retrieval_data.get("reason", "")).strip(),
        )
        return UnifiedDecision(
            should_reply=should_reply,
            skip_reason=skip_reason,
            reply=reply,
            profile_update=profile_update,
            relation_update=relation_update,
            retrieval_plan=retrieval_plan,
        )

    def generate_reply(
        self,
        *,
        prompt_context: PromptContext,
        session_emotion: float,
        global_emotion: float,
        memory_count: int,
        include_notice: bool,
    ) -> str:
        del session_emotion, global_emotion, memory_count
        provider = settings.llm.provider.lower().strip()
        if provider == "kilo":
            reply = self._call_kilo(prompt_context, include_notice=include_notice)
            if reply:
                return reply
            return self._service_unavailable_message()
        logger.warning("Unknown LLM provider '%s', return unavailable notice.", provider)
        return self._service_unavailable_message()

    def is_unavailable_reply(self, reply: str) -> bool:
        return (reply or "").strip() == self._service_unavailable_message().strip()

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
        template = read_required_markdown_asset("prompt/llm_unavailable.md")
        return template.format(admin_id=admin_id)

    @staticmethod
    def _build_system_prompt(prompt_context: PromptContext, *, include_notice: bool) -> str:
        wrapper_template = read_required_markdown_asset("prompt/system_wrapper.md")
        return wrapper_template.format(
            system_core=prompt_context.system_core,
            system_runtime=prompt_context.system_runtime,
            profile_context=prompt_context.profile_context,
            memory_context=prompt_context.memory_context,
            policy_notice=prompt_context.policy_notice if include_notice else "本轮不注入首轮策略提示。",
            parameter_context=prompt_context.parameter_context,
        ).strip()

    def _call_kilo(self, prompt_context: PromptContext, *, include_notice: bool) -> str:
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
            len(self._build_system_prompt(prompt_context, include_notice=include_notice)),
            len(prompt_context.user_message),
            settings.llm.temperature,
        )
        max_attempts = settings.llm.retry_max_attempts
        backoff = settings.llm.retry_backoff_seconds
        last_exc: Exception | None = None
        # Hard time budget to avoid blocking longer than the conversation-window wait timeout.
        # Note: /chat waits for `silence_seconds` before calling the batch executor, so we must
        # budget less than `wait_timeout_seconds` starting from here.
        budget_seconds = settings.rhythm.wait_timeout_seconds - settings.rhythm.silence_seconds - settings.rhythm.cooldown_seconds - 1.0
        deadline = time.monotonic() + max(1.0, budget_seconds)

        for attempt in range(1, max_attempts + 1):
            # If there's not enough remaining time for a full attempt, stop early.
            time_left = deadline - time.monotonic()
            if time_left <= 0:
                break
            if time_left < settings.llm.timeout_seconds:
                break
            try:
                response = client.chat.completions.create(
                    model=settings.llm.model,
                    temperature=settings.llm.temperature,
                    messages=[
                        {
                            "role": "system",
                            "content": self._build_system_prompt(prompt_context, include_notice=include_notice),
                        },
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
                        remaining = deadline - time.monotonic()
                        if remaining > 0:
                            time.sleep(min(sleep_seconds, remaining))
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
        with self._circuit_lock:
            return time.monotonic() < self._circuit_open_until

    def _on_request_success(self) -> None:
        with self._circuit_lock:
            self._consecutive_failures = 0
            self._circuit_open_until = 0.0

    def _on_request_failure(self, exc: Exception) -> None:
        with self._circuit_lock:
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
