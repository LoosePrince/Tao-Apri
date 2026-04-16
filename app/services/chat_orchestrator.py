from dataclasses import dataclass
import logging
import json

from app.core.clock import now_local
from app.core.config import settings
from app.core.metrics import MetricsRegistry
from app.core.markdown_assets import read_required_markdown_asset
from app.domain.models import Message, UserProfile, UserRelation
from app.domain.services.emotion_engine import EmotionEngine
from app.domain.services.identity_service import IdentityService
from app.domain.services.memory_writer import MemoryWriter
from app.domain.services.persona_engine import PersonaEngine
from app.jobs.task_queue import TaskQueue
from app.repos.interfaces import MessageRepo, PreferenceRepo, ProfileRepo, RelationRepo, VectorRepo
from app.services.llm_client import LLMClient
from app.services.prompt_composer import PromptComposer
from app.services.window_preprocessor import WindowPreprocessor

logger = logging.getLogger(__name__)
ASSISTANT_RELATION_ID = "assistant"


@dataclass(slots=True)
class ChatResult:
    session_id: str
    reply: str
    session_emotion: float
    global_emotion: float


class ChatOrchestrator:
    def __init__(
        self,
        *,
        identity_service: IdentityService,
        persona_engine: PersonaEngine,
        emotion_engine: EmotionEngine,
        message_repo: MessageRepo,
        vector_repo: VectorRepo,
        relation_repo: RelationRepo,
        preference_repo: PreferenceRepo,
        profile_repo: ProfileRepo,
        memory_writer: MemoryWriter,
        prompt_composer: PromptComposer,
        llm_client: LLMClient,
        task_queue: TaskQueue,
        window_preprocessor: WindowPreprocessor,
        metrics: MetricsRegistry,
    ) -> None:
        self.identity_service = identity_service
        self.persona_engine = persona_engine
        self.emotion_engine = emotion_engine
        self.message_repo = message_repo
        self.vector_repo = vector_repo
        self.relation_repo = relation_repo
        self.preference_repo = preference_repo
        self.profile_repo = profile_repo
        self.memory_writer = memory_writer
        self.prompt_composer = prompt_composer
        self.llm_client = llm_client
        self.task_queue = task_queue
        self.window_preprocessor = window_preprocessor
        self.metrics = metrics
        self._session_emotion: dict[str, float] = {}

    def _retrieve_memories(self, user_id: str, query: str) -> list[Message]:
        return self.vector_repo.search(
            query=query,
            user_id=user_id,
            limit=settings.retrieval.top_k,
            min_score=settings.retrieval.min_score,
            recency_window_days=settings.retrieval.recency_window_days,
        )

    @staticmethod
    def _merge_memories_by_id(memories: list[Message]) -> list[Message]:
        seen: set[str] = set()
        merged: list[Message] = []
        for memory in memories:
            if memory.message_id in seen:
                continue
            seen.add(memory.message_id)
            merged.append(memory)
        return merged

    @staticmethod
    def _render_template(template: str, values: dict[str, object]) -> str:
        return template.format(**values)

    def _build_retrieval_report(
        self,
        *,
        retrieved_memories: list[Message],
        latest_queries: list[str],
        latest_batch_count: int,
        remaining_retrievals: int,
    ) -> str:
        latest_memory_lines = [f"- {memory.sanitized_content[:120]}" for memory in retrieved_memories[-5:]]
        latest_memories = "\n".join(latest_memory_lines) if latest_memory_lines else "- 无"
        report_template = read_required_markdown_asset("prompt/retrieval_iteration_report.md")
        return self._render_template(
            report_template,
            {
                "latest_queries": ", ".join(latest_queries) if latest_queries else "无",
                "latest_batch_count": latest_batch_count,
                "total_memory_count": len(retrieved_memories),
                "remaining_retrievals": remaining_retrievals,
                "latest_memories": latest_memories,
            },
        )

    def _build_profile_summary(
        self,
        *,
        user_id: str,
        session_emotion: float,
        global_emotion: float,
        current_hour: int,
        current_date: str,
        current_year: int,
        pending_user_text: str = "",
    ) -> tuple[str, str, str, str, str, float, float]:
        recent_messages = self.message_repo.list_by_user(
            user_id=user_id,
            limit=settings.profile.recent_message_limit,
        )
        user_texts = [msg.sanitized_content.strip() for msg in recent_messages if msg.role == "user" and msg.sanitized_content.strip()]
        if pending_user_text.strip():
            user_texts.append(pending_user_text.strip())
        if not user_texts:
            return "", "", "", "", "", 0.0, 0.0
        decision = self.llm_client.generate_profile_decision(
            user_texts=user_texts,
            current_hour=current_hour,
            current_date=current_date,
            current_year=current_year,
            session_emotion=session_emotion,
            global_emotion=global_emotion,
        )
        profile_summary = str(decision.get("profile_summary", "")).strip()
        preference_summary = str(decision.get("preference_summary", "")).strip()
        preferred_address = str(decision.get("preferred_address", "")).strip()[:12]
        tone_preference = str(decision.get("tone_preference", "")).strip()
        schedule_state = str(decision.get("schedule_state", "")).strip()
        fatigue_level = self._clamp01(float(decision.get("fatigue_level", 0.0) or 0.0))
        emotion_peak_level = self._clamp01(float(decision.get("emotion_peak_level", 0.0) or 0.0))

        if not profile_summary:
            profile_summary = "近期表达仍在观察中。"
        if not preference_summary:
            preference_summary = "偏好信息有限，建议继续观察。"
        if not tone_preference:
            tone_preference = "自然中性"
        if not schedule_state:
            schedule_state = "常规节奏"
        return (
            profile_summary,
            preference_summary,
            preferred_address,
            tone_preference,
            schedule_state,
            fatigue_level,
            emotion_peak_level,
        )

    @staticmethod
    def _clamp01(value: float) -> float:
        return max(0.0, min(1.0, value))

    def _update_user_relation_state(self, *, user_id: str, user_message: str, reply: str) -> None:
        relation = self.relation_repo.get(user_id, ASSISTANT_RELATION_ID) or UserRelation(
            source_user_id=user_id,
            target_user_id=ASSISTANT_RELATION_ID,
        )
        relation_json = json.dumps(
            {
                "polarity": relation.polarity,
                "strength": relation.strength,
                "trust_score": relation.trust_score,
                "intimacy_score": relation.intimacy_score,
                "dependency_score": relation.dependency_score,
            },
            ensure_ascii=False,
        )
        decision = self.llm_client.evolve_relation_decision(
            relation_json=relation_json,
            user_message=user_message,
            reply=reply,
        )
        polarity = str(decision.get("polarity", relation.polarity)).strip()
        relation.polarity = polarity if polarity in {"positive", "neutral", "negative"} else relation.polarity
        relation.strength = self._clamp01(float(decision.get("strength", relation.strength) or relation.strength))
        relation.trust_score = self._clamp01(float(decision.get("trust_score", relation.trust_score) or relation.trust_score))
        relation.intimacy_score = self._clamp01(
            float(decision.get("intimacy_score", relation.intimacy_score) or relation.intimacy_score)
        )
        relation.dependency_score = self._clamp01(
            float(decision.get("dependency_score", relation.dependency_score) or relation.dependency_score)
        )
        self.relation_repo.upsert(relation)
        logger.info(
            "Relation evolved | user_id=%s | target=%s | polarity=%s | strength=%.3f | trust=%.3f | intimacy=%.3f | dependency=%.3f",
            user_id,
            ASSISTANT_RELATION_ID,
            relation.polarity,
            relation.strength,
            relation.trust_score,
            relation.intimacy_score,
            relation.dependency_score,
        )

    def _persist_profile(self, profile: UserProfile) -> None:
        self.profile_repo.upsert(profile)

    def _build_group_emotion_context(self, *, viewer_user_id: str) -> tuple[float, str]:
        recent_all = self.message_repo.list_all(limit=120)
        cross_user_msgs = [
            message
            for message in recent_all
            if message.user_id != viewer_user_id and message.role == "user"
        ]
        if not cross_user_msgs:
            return 0.0, "群体情绪：中性平稳。"
        recent_scores = [msg.emotion_score for msg in cross_user_msgs[-20:]]
        decision = self.llm_client.summarize_group_emotion(scores=recent_scores)
        return decision.score, decision.text

    def _apply_cross_access_control(
        self,
        *,
        viewer_user_id: str,
        query: str,
        memories: list[Message],
    ) -> tuple[list[Message], bool, dict[str, int]]:
        visible: list[Message] = []
        cross_candidates = 0
        decision_candidates: list[dict[str, object]] = []
        for memory in memories:
            if memory.user_id == viewer_user_id:
                visible.append(memory)
                continue
            cross_candidates += 1
            relation = self.relation_repo.get(viewer_user_id, memory.user_id)
            preference = self.preference_repo.get(memory.user_id)
            decision_candidates.append(
                {
                    "message_id": memory.message_id,
                    "source_user_id": memory.user_id,
                    "text": memory.sanitized_content,
                    "relation": {
                        "polarity": relation.polarity if relation else "unknown",
                        "strength": relation.strength if relation else 0.0,
                    },
                    "preference": {
                        "share_default": preference.share_default if preference else "deny",
                        "topic_visibility": preference.topic_visibility if preference else {},
                        "explicit_deny_items": preference.explicit_deny_items if preference else [],
                    },
                }
            )
        decision = self.llm_client.decide_cross_access(
            viewer_user_id=viewer_user_id,
            query=query,
            memories=decision_candidates,
        )
        allowed_ids = decision.allowed_message_ids
        for memory in memories:
            if memory.user_id == viewer_user_id:
                continue
            if memory.message_id in allowed_ids:
                visible.append(memory)
        denied_cross_count = max(0, cross_candidates - len([m for m in memories if m.user_id != viewer_user_id and m.message_id in allowed_ids]))
        return visible, denied_cross_count > 0, {
            "cross_candidates": cross_candidates,
            "relation_denied": decision.relation_denied,
            "similarity_denied": decision.similarity_denied,
            "preference_denied": decision.preference_denied,
            "cross_allowed": max(0, cross_candidates - denied_cross_count),
        }

    def handle_message(self, *, user_id: str, user_message: str) -> ChatResult:
        return self.handle_window_batch(user_id=user_id, user_messages=[user_message], abort_requested=False)

    def handle_window_batch(self, *, user_id: str, user_messages: list[str], abort_requested: bool) -> ChatResult:
        raw_user_message = "\n".join(msg.strip() for msg in user_messages if msg.strip())
        preprocessed = self.window_preprocessor.preprocess(user_messages)
        user_message = preprocessed.merged_user_message or raw_user_message
        self.metrics.inc("long_text_placeholder_count", preprocessed.long_placeholder_count)
        if preprocessed.used_window_summary:
            self.metrics.inc("window_compress_count")
        if abort_requested:
            self.metrics.inc("abort_batch_count")

        logger.info("Chat handling started | user_id=%s", user_id)
        logger.debug("Incoming user message | user_id=%s | text=%s", user_id, user_message)
        now = now_local()
        _, session = self.identity_service.ensure_user_and_session(user_id)
        logger.debug(
            "Session ensured | user_id=%s | session_id=%s | turn_count=%s",
            user_id,
            session.session_id,
            session.turn_count,
        )

        last_session_emotion = self._session_emotion.get(session.session_id, 0.0)
        message_score = self.emotion_engine.score_message(user_message)
        emotion_state = self.emotion_engine.update(last_session_emotion, message_score)
        self._session_emotion[session.session_id] = emotion_state.session_emotion
        logger.info(
            "Emotion updated | user_id=%s | session_id=%s | message_score=%.3f | session_emotion=%.3f | global_emotion=%.3f",
            user_id,
            session.session_id,
            message_score,
            emotion_state.session_emotion,
            emotion_state.global_emotion,
        )

        (
            profile_summary_generated,
            preference_summary_generated,
            preferred_address_generated,
            tone_preference_generated,
            schedule_state_generated,
            fatigue_level_generated,
            emotion_peak_generated,
        ) = self._build_profile_summary(
            user_id=user_id,
            session_emotion=emotion_state.session_emotion,
            global_emotion=emotion_state.global_emotion,
            current_hour=now.hour,
            current_date=now.date().isoformat(),
            current_year=now.year,
            pending_user_text=user_message,
        )
        if profile_summary_generated:
            generated_profile = UserProfile(
                user_id=user_id,
                profile_summary=profile_summary_generated,
                preference_summary=preference_summary_generated,
                preferred_address=preferred_address_generated,
                tone_preference=tone_preference_generated,
                schedule_state=schedule_state_generated,
                fatigue_level=fatigue_level_generated,
                emotion_peak_level=emotion_peak_generated,
            )
            self.task_queue.submit(self._persist_profile, generated_profile)
            logger.info(
                "Profile updated | user_id=%s | profile_summary=%s | preference_summary=%s | preferred_address=%s | tone_preference=%s | schedule_state=%s | fatigue_level=%.3f | emotion_peak=%.3f",
                user_id,
                profile_summary_generated,
                preference_summary_generated,
                preferred_address_generated,
                tone_preference_generated,
                schedule_state_generated,
                fatigue_level_generated,
                emotion_peak_generated,
            )

        retrieval_plan = None
        retrieval_round = 0
        retrieved: list[Message] = []
        remaining_retrievals = settings.retrieval.max_rounds
        latest_queries: list[str] = []
        latest_batch_count = 0
        while remaining_retrievals > 0:
            retrieval_report = self._build_retrieval_report(
                retrieved_memories=retrieved,
                latest_queries=latest_queries,
                latest_batch_count=latest_batch_count,
                remaining_retrievals=remaining_retrievals,
            )
            retrieval_plan = self.llm_client.plan_retrieval(
                user_message=user_message,
                retrieval_report=retrieval_report,
                remaining_retrievals=remaining_retrievals,
            )
            if not retrieval_plan.should_retrieve:
                break
            latest_queries = retrieval_plan.queries
            round_batch: list[Message] = []
            for query in latest_queries:
                round_batch.extend(self._retrieve_memories(user_id=user_id, query=query))
            before_merge = len(retrieved)
            retrieved = self._merge_memories_by_id(retrieved + round_batch)
            latest_batch_count = len(retrieved) - before_merge
            retrieval_round += 1
            remaining_retrievals -= 1
            if latest_batch_count <= 0:
                continue

        if retrieval_plan is None:
            retrieval_plan = self.llm_client.plan_retrieval(
                user_message=user_message,
                retrieval_report="",
                remaining_retrievals=remaining_retrievals,
            )
        memories = retrieved
        memories, cross_access_denied, access_stats = self._apply_cross_access_control(
            viewer_user_id=user_id,
            query=user_message,
            memories=memories,
        )
        logger.info(
            "Memories retrieved | user_id=%s | count=%s | rounds=%s | should_retrieve=%s | queries=%s | reason=%s | remaining=%s",
            user_id,
            len(memories),
            retrieval_round,
            retrieval_plan.should_retrieve,
            retrieval_plan.queries,
            retrieval_plan.reason,
            remaining_retrievals,
        )
        logger.debug(
            "Retrieved memory snippets | user_id=%s | memories=%s",
            user_id,
            [m.sanitized_content for m in memories],
        )
        logger.info(
            "Retrieval explainability | user_id=%s | entries=%s",
            user_id,
            [
                {
                    "message_id": m.message_id,
                    "user_id": m.user_id,
                    "meta": m.retrieval_meta,
                }
                for m in memories
            ],
        )
        profile = self.profile_repo.get(user_id)
        profile_summary = ""
        if profile:
            profile_parts = [profile.profile_summary.strip(), profile.preference_summary.strip()]
            if profile.preferred_address.strip():
                profile_parts.append(f"称呼偏好：优先称呼用户为“{profile.preferred_address.strip()}”")
            if profile.tone_preference.strip():
                profile_parts.append(f"语气偏好：{profile.tone_preference.strip()}")
            if profile.schedule_state.strip():
                profile_parts.append(f"周期状态：{profile.schedule_state.strip()}")
            profile_parts.append(f"疲惫度：{profile.fatigue_level:.2f}")
            profile_parts.append(f"情绪波峰：{profile.emotion_peak_level:.2f}")
            profile_summary = "\n".join(part for part in profile_parts if part)
        elif profile_summary_generated:
            profile_parts = [profile_summary_generated, preference_summary_generated]
            if preferred_address_generated.strip():
                profile_parts.append(f"称呼偏好：优先称呼用户为“{preferred_address_generated.strip()}”")
            if tone_preference_generated.strip():
                profile_parts.append(f"语气偏好：{tone_preference_generated.strip()}")
            if schedule_state_generated.strip():
                profile_parts.append(f"周期状态：{schedule_state_generated.strip()}")
            profile_parts.append(f"疲惫度：{fatigue_level_generated:.2f}")
            profile_parts.append(f"情绪波峰：{emotion_peak_generated:.2f}")
            profile_summary = "\n".join(part for part in profile_parts if part)
        group_emotion_avg, group_emotion_text = self._build_group_emotion_context(viewer_user_id=user_id)
        profile_summary = (profile_summary + "\n" + group_emotion_text).strip() if profile_summary else group_emotion_text
        # Decide policy inputs: prefer stored profile fatigue/emotion_peak when available,
        # otherwise fall back to generated profile values.
        fatigue_level_for_decider = profile.fatigue_level if profile else fatigue_level_generated
        emotion_peak_level_for_decider = profile.emotion_peak_level if profile else emotion_peak_generated
        logger.debug(
            "Profile selected for prompt | user_id=%s | has_profile=%s | profile_len=%s",
            user_id,
            bool(profile_summary),
            len(profile_summary),
        )
        logger.info(
            "Generation chain | user_id=%s | relation_allowed=%s | relation_denied=%s | similarity_denied=%s | preference_denied=%s | profile_injected=%s | group_emotion_avg=%.3f",
            user_id,
            access_stats["cross_allowed"],
            access_stats["relation_denied"],
            access_stats["similarity_denied"],
            access_stats["preference_denied"],
            bool(profile_summary),
            group_emotion_avg,
        )
        persona = self.persona_engine.get_runtime_persona(now)
        prompt_ctx = self.prompt_composer.compose(
            now=now,
            viewer_user_id=user_id,
            viewer_profile_summary=profile_summary,
            persona=persona,
            session_emotion=emotion_state.session_emotion,
            global_emotion=emotion_state.global_emotion,
            memories=memories,
            user_message=user_message,
        )
        if cross_access_denied and "跨对话模糊参考" not in prompt_ctx.memory_context:
            prompt_ctx.memory_context += "\n- 跨对话信息当前不可访问；若被问及他人细节，请明确回答“我不知道”。"
        logger.debug(
            "Prompt context composed | user_id=%s | system_core_len=%s | system_runtime_len=%s | memory_context_len=%s",
            user_id,
            len(prompt_ctx.system_core),
            len(prompt_ctx.system_runtime),
            len(prompt_ctx.memory_context),
        )

        should_reply_decider = getattr(self.llm_client, "decide_should_reply", None)
        should_reply = True
        skip_reason = ""
        if callable(should_reply_decider):
            decision = should_reply_decider(
                user_message=user_message,
                session_emotion=emotion_state.session_emotion,
                global_emotion=emotion_state.global_emotion,
                fatigue_level=fatigue_level_for_decider,
                emotion_peak_level=emotion_peak_level_for_decider,
                memory_count=len(memories),
                current_hour=now.hour,
                current_date=now.date().isoformat(),
                current_year=now.year,
            )
            if isinstance(decision, dict):
                should_reply = bool(decision.get("should_reply", True))
                skip_reason = str(decision.get("reason", "")).strip()
            else:
                should_reply = bool(getattr(decision, "should_reply", True))
                skip_reason = str(getattr(decision, "reason", "")).strip()

        if not should_reply:
            if not skip_reason:
                skip_reason = "skip:should_reply=false"
            self.metrics.inc("reply_skipped_count")
            logger.info(
                "Skip assistant reply by should_reply=false | user_id=%s | session_id=%s | reason=%s",
                user_id,
                session.session_id,
                skip_reason,
            )
            self.memory_writer.write(
                session_id=session.session_id,
                user_id=user_id,
                role="user",
                content=user_message,
                emotion_score=message_score,
            )
            logger.debug("User message persisted (reply skipped) | user_id=%s | session_id=%s", user_id, session.session_id)
            return ChatResult(
                session_id=session.session_id,
                reply="",
                session_emotion=emotion_state.session_emotion,
                global_emotion=emotion_state.global_emotion,
            )

        reply = self.llm_client.generate_reply(
            prompt_context=prompt_ctx,
            memory_count=len(memories),
            session_emotion=emotion_state.session_emotion,
            global_emotion=emotion_state.global_emotion,
            include_notice=session.turn_count == 0 and settings.persona.policy_notice_on_first_turn,
        )
        logger.info("LLM reply generated | user_id=%s | reply_len=%s", user_id, len(reply))
        logger.debug("LLM reply text | user_id=%s | reply=%s", user_id, reply)

        if self.llm_client.is_unavailable_reply(reply):
            logger.warning(
                "Chat failed, skip message persistence | user_id=%s | session_id=%s",
                user_id,
                session.session_id,
            )
            return ChatResult(
                session_id=session.session_id,
                reply=reply,
                session_emotion=emotion_state.session_emotion,
                global_emotion=emotion_state.global_emotion,
            )

        self.memory_writer.write(
            session_id=session.session_id,
            user_id=user_id,
            role="user",
            content=user_message,
            emotion_score=message_score,
        )
        logger.debug("User message persisted | user_id=%s | session_id=%s", user_id, session.session_id)

        self.memory_writer.write(
            session_id=session.session_id,
            user_id=user_id,
            role="assistant",
            content=reply,
            emotion_score=emotion_state.session_emotion,
        )
        self.task_queue.submit(
            self._update_user_relation_state,
            user_id=user_id,
            user_message=raw_user_message or user_message,
            reply=reply,
        )
        logger.debug("Assistant message persisted | user_id=%s | session_id=%s", user_id, session.session_id)

        session.turn_count += 1
        self.identity_service.session_repo.upsert(session)
        logger.info(
            "Chat handling finished | user_id=%s | session_id=%s | turn_count=%s",
            user_id,
            session.session_id,
            session.turn_count,
        )
        return ChatResult(
            session_id=session.session_id,
            reply=reply,
            session_emotion=emotion_state.session_emotion,
            global_emotion=emotion_state.global_emotion,
        )
