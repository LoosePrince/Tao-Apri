from dataclasses import dataclass
import logging
from collections import Counter

from app.core.clock import now_local
from app.core.config import settings
from app.core.markdown_assets import read_required_markdown_asset
from app.domain.models import Message, UserProfile, UserRelation
from app.domain.services.emotion_engine import EmotionEngine
from app.domain.services.identity_service import IdentityService
from app.domain.services.memory_writer import MemoryWriter
from app.domain.services.persona_engine import PersonaEngine
from app.repos.interfaces import MessageRepo, PreferenceRepo, ProfileRepo, RelationRepo, VectorRepo
from app.services.llm_client import LLMClient
from app.services.prompt_composer import PromptComposer

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
    ) -> tuple[str, str, str, float, float]:
        recent_messages = self.message_repo.list_by_user(
            user_id=user_id,
            limit=settings.profile.recent_message_limit,
        )
        user_texts = [msg.sanitized_content.strip() for msg in recent_messages if msg.role == "user" and msg.sanitized_content.strip()]
        if not user_texts:
            return "", "", "", 0.0, 0.0

        topic_counter = Counter(self._infer_topic(text) for text in user_texts)
        top_topics = [topic for topic, _ in topic_counter.most_common(3)]
        topic_text = "、".join(top_topics) if top_topics else "日常近况"
        avg_len = sum(len(text) for text in user_texts) / max(1, len(user_texts))
        style_text = "表达更简短直接" if avg_len < 20 else "表达相对完整，愿意展开描述"
        preference_cues: list[str] = []
        for text in user_texts:
            if any(keyword in text for keyword in ("喜欢", "爱", "想要")):
                preference_cues.append("偏好表达积极倾向")
            if any(keyword in text for keyword in ("不想", "讨厌", "别", "不要")):
                preference_cues.append("会明确表达边界与不偏好")
        preference_summary = "；".join(dict.fromkeys(preference_cues)) or "偏好信息有限，建议继续观察。"

        schedule_state = "常规节奏"
        primary_topic = top_topics[0] if top_topics else "日常近况"
        if primary_topic == "学习与考试":
            schedule_state = "学习周期"
        elif primary_topic == "工作与职业":
            schedule_state = "工作周期"
        elif primary_topic in ("作息与健康", "情绪与关系"):
            schedule_state = "休整周期"
        if 23 <= current_hour or current_hour <= 5:
            schedule_state += "（夜间阶段）"

        fatigue_hits = sum(
            1
            for text in user_texts
            if any(token in text for token in ("累", "困", "失眠", "疲惫", "没精神", "头疼"))
        )
        fatigue_level = fatigue_hits / max(1, len(user_texts))
        if 23 <= current_hour or current_hour <= 5:
            fatigue_level += 0.15
        if session_emotion < -0.2:
            fatigue_level += 0.1
        fatigue_level = self._clamp01(fatigue_level)

        peak_from_messages = max((abs(msg.emotion_score) for msg in recent_messages if msg.role == "user"), default=0.0)
        emotion_peak_level = self._clamp01(max(peak_from_messages, abs(session_emotion), abs(global_emotion)))

        profile_summary = f"近期关注话题：{topic_text}；{style_text}。"
        return profile_summary, preference_summary, schedule_state, fatigue_level, emotion_peak_level

    @staticmethod
    def _clamp01(value: float) -> float:
        return max(0.0, min(1.0, value))

    def _update_user_relation_state(self, *, user_id: str, user_message: str, reply: str) -> None:
        relation = self.relation_repo.get(user_id, ASSISTANT_RELATION_ID) or UserRelation(
            source_user_id=user_id,
            target_user_id=ASSISTANT_RELATION_ID,
        )
        lowered = user_message.lower()
        intimacy_delta = 0.03 + min(len(user_message), 120) / 3000.0
        trust_delta = 0.02
        dependency_delta = 0.0

        if any(token in user_message for token in ("我觉得", "我最近", "我有点", "我很")):
            intimacy_delta += 0.03
        if any(token in user_message for token in ("你觉得", "你能", "帮我", "怎么办", "建议")):
            trust_delta += 0.05
        if any(token in user_message for token in ("你告诉我", "只能靠你", "离不开你", "全靠你")):
            dependency_delta += 0.08
        if any(token in lowered for token in ("滚", "烦", "讨厌", "闭嘴")):
            intimacy_delta -= 0.06
            trust_delta -= 0.08
        if any(token in reply for token in ("可以", "建议", "陪你", "一起")):
            trust_delta += 0.02

        relation.intimacy_score = self._clamp01(relation.intimacy_score * 0.98 + intimacy_delta)
        relation.trust_score = self._clamp01(relation.trust_score * 0.98 + trust_delta)
        relation.dependency_score = self._clamp01(relation.dependency_score * 0.98 + dependency_delta)
        relation.strength = round((relation.intimacy_score + relation.trust_score) / 2.0, 4)
        if relation.strength >= 0.65:
            relation.polarity = "positive"
        elif relation.strength <= 0.30:
            relation.polarity = "negative"
        else:
            relation.polarity = "neutral"
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

    @staticmethod
    def _infer_topic(text: str) -> str:
        if any(token in text for token in ("学习", "考试", "作业", "复习", "成绩")):
            return "学习与考试"
        if any(token in text for token in ("工作", "加班", "同事", "面试", "项目")):
            return "工作与职业"
        if any(token in text for token in ("家人", "恋爱", "朋友", "关系", "吵架")):
            return "情绪与关系"
        if any(token in text for token in ("失眠", "作息", "健康", "疲惫", "生病")):
            return "作息与健康"
        return "日常近况"

    def _relation_allows_cross(self, viewer_user_id: str, source_user_id: str) -> tuple[bool, float]:
        relation = self.relation_repo.get(viewer_user_id, source_user_id)
        if relation is None:
            return False, settings.retrieval.cross_negative_threshold
        if relation.strength < settings.retrieval.relation_access_min_strength:
            return False, settings.retrieval.cross_negative_threshold
        if relation.polarity == "positive":
            return True, settings.retrieval.cross_positive_threshold
        if relation.polarity == "negative":
            return False, settings.retrieval.cross_negative_threshold
        return True, settings.retrieval.cross_neutral_threshold

    @staticmethod
    def _memory_similarity(query: str, text: str) -> float:
        query_norm = query.lower().replace("，", " ").replace(",", " ").strip()
        text_norm = text.lower().replace("，", " ").replace(",", " ").strip()
        query_tokens = {token for token in query_norm.split() if token}
        text_tokens = {token for token in text_norm.split() if token}
        if len(query_tokens) <= 1:
            query_tokens = {ch for ch in query_norm if not ch.isspace()}
        if len(text_tokens) <= 1:
            text_tokens = {ch for ch in text_norm if not ch.isspace()}
        if not query_tokens or not text_tokens:
            return 0.0
        overlap = len(query_tokens.intersection(text_tokens))
        return overlap / max(1, len(query_tokens))

    def _preference_allows(self, source_user_id: str, text: str) -> bool:
        pref = self.preference_repo.get(source_user_id)
        if pref is None:
            return False
        if pref.share_default == "deny":
            return False
        topic = self._infer_topic(text)
        topic_visibility = pref.topic_visibility.get(topic, "allow")
        if topic_visibility == "deny":
            return False
        lowered = text.lower()
        return not any(item.lower() in lowered for item in pref.explicit_deny_items)

    def _apply_cross_access_control(
        self,
        *,
        viewer_user_id: str,
        query: str,
        memories: list[Message],
    ) -> tuple[list[Message], bool, dict[str, int]]:
        visible: list[Message] = []
        denied_cross_count = 0
        relation_denied = 0
        similarity_denied = 0
        preference_denied = 0
        cross_candidates = 0
        for memory in memories:
            if memory.user_id == viewer_user_id:
                visible.append(memory)
                continue
            cross_candidates += 1
            relation_allowed, relation_threshold = self._relation_allows_cross(viewer_user_id, memory.user_id)
            if not relation_allowed:
                denied_cross_count += 1
                relation_denied += 1
                continue
            if self._memory_similarity(query, memory.sanitized_content) < relation_threshold:
                denied_cross_count += 1
                similarity_denied += 1
                continue
            if not self._preference_allows(memory.user_id, memory.sanitized_content):
                denied_cross_count += 1
                preference_denied += 1
                continue
            visible.append(memory)
        return visible, denied_cross_count > 0, {
            "cross_candidates": cross_candidates,
            "relation_denied": relation_denied,
            "similarity_denied": similarity_denied,
            "preference_denied": preference_denied,
            "cross_allowed": max(0, cross_candidates - denied_cross_count),
        }

    def handle_message(self, *, user_id: str, user_message: str) -> ChatResult:
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

        self.memory_writer.write(
            session_id=session.session_id,
            user_id=user_id,
            role="user",
            content=user_message,
            emotion_score=message_score,
        )
        logger.debug("User message persisted | user_id=%s | session_id=%s", user_id, session.session_id)
        (
            profile_summary_generated,
            preference_summary_generated,
            schedule_state_generated,
            fatigue_level_generated,
            emotion_peak_generated,
        ) = self._build_profile_summary(
            user_id=user_id,
            session_emotion=emotion_state.session_emotion,
            global_emotion=emotion_state.global_emotion,
            current_hour=now.hour,
        )
        if profile_summary_generated:
            self.profile_repo.upsert(
                UserProfile(
                    user_id=user_id,
                    profile_summary=profile_summary_generated,
                    preference_summary=preference_summary_generated,
                    schedule_state=schedule_state_generated,
                    fatigue_level=fatigue_level_generated,
                    emotion_peak_level=emotion_peak_generated,
                )
            )
            logger.info(
                "Profile updated | user_id=%s | profile_summary=%s | preference_summary=%s | schedule_state=%s | fatigue_level=%.3f | emotion_peak=%.3f",
                user_id,
                profile_summary_generated,
                preference_summary_generated,
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
        profile = self.profile_repo.get(user_id)
        profile_summary = ""
        if profile:
            profile_parts = [profile.profile_summary.strip(), profile.preference_summary.strip()]
            if profile.schedule_state.strip():
                profile_parts.append(f"周期状态：{profile.schedule_state.strip()}")
            profile_parts.append(f"疲惫度：{profile.fatigue_level:.2f}")
            profile_parts.append(f"情绪波峰：{profile.emotion_peak_level:.2f}")
            profile_summary = "\n".join(part for part in profile_parts if part)
        logger.debug(
            "Profile selected for prompt | user_id=%s | has_profile=%s | profile_len=%s",
            user_id,
            bool(profile_summary),
            len(profile_summary),
        )
        logger.info(
            "Generation chain | user_id=%s | relation_allowed=%s | relation_denied=%s | similarity_denied=%s | preference_denied=%s | profile_injected=%s",
            user_id,
            access_stats["cross_allowed"],
            access_stats["relation_denied"],
            access_stats["similarity_denied"],
            access_stats["preference_denied"],
            bool(profile_summary),
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

        reply = self.llm_client.generate_reply(
            prompt_context=prompt_ctx,
            memory_count=len(memories),
            session_emotion=emotion_state.session_emotion,
            global_emotion=emotion_state.global_emotion,
            include_notice=session.turn_count == 0 and settings.persona.policy_notice_on_first_turn,
        )
        logger.info("LLM reply generated | user_id=%s | reply_len=%s", user_id, len(reply))
        logger.debug("LLM reply text | user_id=%s | reply=%s", user_id, reply)

        self.memory_writer.write(
            session_id=session.session_id,
            user_id=user_id,
            role="assistant",
            content=reply,
            emotion_score=emotion_state.session_emotion,
        )
        self._update_user_relation_state(user_id=user_id, user_message=user_message, reply=reply)
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
