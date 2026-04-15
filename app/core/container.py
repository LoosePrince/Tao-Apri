from app.core.config import settings
from app.domain.services.emotion_engine import EmotionEngine
from app.domain.services.identity_service import IdentityService
from app.domain.services.memory_writer import MemoryWriter
from app.domain.services.persona_engine import PersonaEngine
from app.jobs.emotion_aggregator import EmotionAggregatorJob
from app.jobs.periodic_scheduler import PeriodicScheduler
from app.jobs.task_queue import TaskQueue
from app.repos.sqlite_repo import (
    SQLiteEmotionStateRepo,
    SQLiteFactRepo,
    SQLiteMessageRepo,
    SQLitePreferenceRepo,
    SQLiteProfileRepo,
    SQLiteRelationRepo,
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
        self.relation_repo = SQLiteRelationRepo(self.store)
        self.preference_repo = SQLitePreferenceRepo(self.store)
        self.profile_repo = SQLiteProfileRepo(self.store)

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
        self.task_queue = TaskQueue(
            enabled=settings.jobs.enabled,
            worker_count=settings.jobs.worker_count,
            queue_size=settings.jobs.queue_size,
        )
        self.emotion_aggregator_job = EmotionAggregatorJob(
            message_repo=self.message_repo,
            emotion_engine=self.emotion_engine,
        )
        self.periodic_scheduler = PeriodicScheduler(enabled=settings.jobs.maintenance_enabled)
        self.periodic_scheduler.add_job(
            name="emotion_aggregation",
            interval_seconds=settings.jobs.maintenance_interval_seconds,
            job=lambda: self.emotion_aggregator_job.run(window_minutes=settings.jobs.emotion_window_minutes),
        )
        self.periodic_scheduler.add_job(
            name="vector_maintenance",
            interval_seconds=settings.jobs.maintenance_interval_seconds,
            job=self.vector_repo.run_maintenance,
        )
        self.chat_orchestrator = ChatOrchestrator(
            identity_service=self.identity_service,
            persona_engine=self.persona_engine,
            emotion_engine=self.emotion_engine,
            message_repo=self.message_repo,
            vector_repo=self.vector_repo,
            relation_repo=self.relation_repo,
            preference_repo=self.preference_repo,
            profile_repo=self.profile_repo,
            memory_writer=self.memory_writer,
            prompt_composer=self.prompt_composer,
            llm_client=self.llm_client,
            task_queue=self.task_queue,
        )


container = Container()
