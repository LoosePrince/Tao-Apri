from dataclasses import dataclass
from datetime import datetime, timezone

from app.core.config import settings
from app.domain.models import Message
from app.domain.services.emotion_engine import EmotionEngine
from app.domain.services.identity_service import IdentityService
from app.domain.services.memory_writer import MemoryWriter
from app.domain.services.persona_engine import PersonaEngine
from app.repos.interfaces import MessageRepo, VectorRepo
from app.services.llm_client import LLMClient
from app.services.prompt_composer import PromptComposer


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
        memory_writer: MemoryWriter,
        prompt_composer: PromptComposer,
        llm_client: LLMClient,
    ) -> None:
        self.identity_service = identity_service
        self.persona_engine = persona_engine
        self.emotion_engine = emotion_engine
        self.message_repo = message_repo
        self.vector_repo = vector_repo
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

    def handle_message(self, *, user_id: str, user_message: str) -> ChatResult:
        now = datetime.now(timezone.utc)
        _, session = self.identity_service.ensure_user_and_session(user_id)

        last_session_emotion = self._session_emotion.get(session.session_id, 0.0)
        message_score = self.emotion_engine.score_message(user_message)
        emotion_state = self.emotion_engine.update(last_session_emotion, message_score)
        self._session_emotion[session.session_id] = emotion_state.session_emotion

        self.memory_writer.write(
            session_id=session.session_id,
            user_id=user_id,
            role="user",
            content=user_message,
            emotion_score=message_score,
        )

        memories = self._retrieve_memories(user_id=user_id, query=user_message)
        persona = self.persona_engine.get_runtime_persona(now)
        prompt_ctx = self.prompt_composer.compose(
            now=now,
            persona=persona,
            session_emotion=emotion_state.session_emotion,
            global_emotion=emotion_state.global_emotion,
            memories=memories,
            user_message=user_message,
        )

        reply = self.llm_client.generate_reply(
            prompt_context=prompt_ctx,
            memory_count=len(memories),
            session_emotion=emotion_state.session_emotion,
            global_emotion=emotion_state.global_emotion,
            include_notice=session.turn_count == 0 and settings.persona.policy_notice_on_first_turn,
        )

        self.memory_writer.write(
            session_id=session.session_id,
            user_id=user_id,
            role="assistant",
            content=reply,
            emotion_score=emotion_state.session_emotion,
        )

        session.turn_count += 1
        self.identity_service.session_repo.upsert(session)
        return ChatResult(
            session_id=session.session_id,
            reply=reply,
            session_emotion=emotion_state.session_emotion,
            global_emotion=emotion_state.global_emotion,
        )
