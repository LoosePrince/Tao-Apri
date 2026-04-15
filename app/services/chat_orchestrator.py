from dataclasses import dataclass
import logging

from app.core.clock import now_local
from app.core.config import settings
from app.domain.models import Message
from app.domain.services.emotion_engine import EmotionEngine
from app.domain.services.identity_service import IdentityService
from app.domain.services.memory_writer import MemoryWriter
from app.domain.services.persona_engine import PersonaEngine
from app.repos.interfaces import MessageRepo, PreferenceRepo, ProfileRepo, RelationRepo, VectorRepo
from app.services.llm_client import LLMClient
from app.services.prompt_composer import PromptComposer

logger = logging.getLogger(__name__)


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
    ) -> tuple[list[Message], bool]:
        visible: list[Message] = []
        denied_cross_count = 0
        for memory in memories:
            if memory.user_id == viewer_user_id:
                visible.append(memory)
                continue
            relation_allowed, relation_threshold = self._relation_allows_cross(viewer_user_id, memory.user_id)
            if not relation_allowed:
                denied_cross_count += 1
                continue
            if self._memory_similarity(query, memory.sanitized_content) < relation_threshold:
                denied_cross_count += 1
                continue
            if not self._preference_allows(memory.user_id, memory.sanitized_content):
                denied_cross_count += 1
                continue
            visible.append(memory)
        return visible, denied_cross_count > 0

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

        memories = self._retrieve_memories(user_id=user_id, query=user_message)
        memories, cross_access_denied = self._apply_cross_access_control(
            viewer_user_id=user_id,
            query=user_message,
            memories=memories,
        )
        logger.info("Memories retrieved | user_id=%s | count=%s", user_id, len(memories))
        logger.debug(
            "Retrieved memory snippets | user_id=%s | memories=%s",
            user_id,
            [m.sanitized_content for m in memories],
        )
        profile = self.profile_repo.get(user_id)
        profile_summary = (
            profile.profile_summary if profile and profile.profile_summary.strip() else "该用户偏好自然交流，避免过度确定表达。"
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
