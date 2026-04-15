from app.core.config import settings
from app.domain.services.emotion_engine import EmotionEngine
from app.domain.services.identity_service import IdentityService
from app.domain.services.memory_writer import MemoryWriter
from app.domain.services.persona_engine import PersonaEngine
from app.repos.sqlite_repo import (
    SQLiteEmotionStateRepo,
    SQLiteFactRepo,
    SQLiteMessageRepo,
    SQLiteSessionRepo,
    SQLiteStore,
    SQLiteUserRepo,
    SQLiteVectorRepo,
)
from app.services.chat_orchestrator import ChatOrchestrator
from app.services.llm_client import LLMClient
from app.services.prompt_composer import PromptComposer


class Container:
    def __init__(self) -> None:
        self.store = SQLiteStore(settings.storage.sqlite_db_path)
        self.user_repo = SQLiteUserRepo(self.store)
        self.session_repo = SQLiteSessionRepo(self.store)
        self.message_repo = SQLiteMessageRepo(self.store)
        self.fact_repo = SQLiteFactRepo(self.store)
        self.vector_repo = SQLiteVectorRepo(self.store)
        self.emotion_state_repo = SQLiteEmotionStateRepo(self.store)

        self.identity_service = IdentityService(self.user_repo, self.session_repo)
        self.persona_engine = PersonaEngine()
        self.emotion_engine = EmotionEngine(
            decay=settings.emotion.decay,
            gain=settings.emotion.gain,
            max_history=settings.emotion.max_history,
            state_repo=self.emotion_state_repo,
        )
        self.memory_writer = MemoryWriter(
            message_repo=self.message_repo,
            vector_repo=self.vector_repo,
            fact_repo=self.fact_repo,
        )
        self.prompt_composer = PromptComposer()
        self.llm_client = LLMClient()
        self.chat_orchestrator = ChatOrchestrator(
            identity_service=self.identity_service,
            persona_engine=self.persona_engine,
            emotion_engine=self.emotion_engine,
            message_repo=self.message_repo,
            vector_repo=self.vector_repo,
            memory_writer=self.memory_writer,
            prompt_composer=self.prompt_composer,
            llm_client=self.llm_client,
        )


container = Container()
