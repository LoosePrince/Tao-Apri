from app.core.config import settings
from app.core.metrics import MetricsRegistry
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
from app.services.conversation_window_manager import ConversationWindowManager
from app.services.llm_client import LLMClient
from app.services.image_understanding_service import ImageUnderstandingService
from app.services.prompt_composer import PromptComposer
from app.services.window_preprocessor import WindowPreprocessor
from app.domain.conversation_scope import ConversationScope


class Container:
    def __init__(self) -> None:
        import threading

        self._lock = threading.RLock()
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
        self.image_understanding_service = ImageUnderstandingService(llm_client=self.llm_client)
        self.metrics = MetricsRegistry()
        self.window_preprocessor = WindowPreprocessor(llm_client=self.llm_client)
        self.task_queue = TaskQueue(
            enabled=settings.jobs.enabled,
            worker_count=settings.jobs.worker_count,
            queue_size=settings.jobs.queue_size,
            max_retries=settings.jobs.max_retries,
            dead_letter_limit=settings.jobs.dead_letter_limit,
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
            image_understanding_service=self.image_understanding_service,
            task_queue=self.task_queue,
            window_preprocessor=self.window_preprocessor,
            metrics=self.metrics,
        )
        self.window_manager = ConversationWindowManager(
            batch_executor=lambda scope, batch, abort, nickname, source_message_id, attachments, group_hints: self.chat_orchestrator.handle_window_batch(
                scope=scope,
                user_messages=batch,
                abort_requested=abort,
                nickname=nickname,
                source_message_id=source_message_id,
                attachments=attachments,
                group_hints=group_hints,
            ),
            metrics=self.metrics,
        )

    def apply_runtime_settings(self, new_settings: "Settings") -> dict[str, object]:
        """
        将 new_settings 应用到运行实例，并在需要时重建关键组件。

        注意：storage 被计划为 read_only，调用方应避免修改 storage 配置。
        """
        # Local import to avoid circular imports.
        from app.core.config import Settings as SettingsModel

        if not isinstance(new_settings, SettingsModel):
            raise TypeError(f"new_settings must be Settings, got: {type(new_settings)}")

        with self._lock:
            old_dump = settings.model_dump()
            new_dump = new_settings.model_dump()

            emotion_changed = old_dump["emotion"] != new_dump["emotion"]
            llm_rebuild_keys = {"api_key", "base_url", "model", "timeout_seconds", "provider"}
            llm_changed = any(old_dump["llm"].get(k) != new_dump["llm"].get(k) for k in llm_rebuild_keys)

            jobs_queue_keys = {"enabled", "worker_count", "queue_size", "max_retries", "dead_letter_limit"}
            jobs_queue_changed = any(old_dump["jobs"].get(k) != new_dump["jobs"].get(k) for k in jobs_queue_keys)

            scheduler_rebuild_keys = {"maintenance_enabled", "maintenance_interval_seconds"}
            scheduler_changed = any(old_dump["jobs"].get(k) != new_dump["jobs"].get(k) for k in scheduler_rebuild_keys)

            # 先更新全局 settings，让后续重建读到新值。
            for top_key in SettingsModel.model_fields.keys():
                setattr(settings, top_key, getattr(new_settings, top_key))

            rebuilt: list[str] = []

            if llm_changed:
                self.llm_client = LLMClient()
                self.image_understanding_service = ImageUnderstandingService(llm_client=self.llm_client)
                self.window_preprocessor = WindowPreprocessor(llm_client=self.llm_client)
                rebuilt.append("llm_client")
                rebuilt.append("image_understanding_service")
                rebuilt.append("window_preprocessor")

            if emotion_changed:
                self.emotion_engine = EmotionEngine(
                    decay=settings.emotion.decay,
                    gain=settings.emotion.gain,
                    max_history=settings.emotion.max_history,
                    state_repo=self.emotion_state_repo,
                )
                self.emotion_aggregator_job = EmotionAggregatorJob(
                    message_repo=self.message_repo,
                    emotion_engine=self.emotion_engine,
                )
                rebuilt.append("emotion_engine")

            if jobs_queue_changed:
                # Stop old queue to avoid mixed worker pools.
                self.task_queue.stop()
                self.task_queue = TaskQueue(
                    enabled=settings.jobs.enabled,
                    worker_count=settings.jobs.worker_count,
                    queue_size=settings.jobs.queue_size,
                    max_retries=settings.jobs.max_retries,
                    dead_letter_limit=settings.jobs.dead_letter_limit,
                )
                rebuilt.append("task_queue")
                self.task_queue.start()

            # ChatOrchestrator binds emotion_engine/llm_client/task_queue/window_preprocessor at init.
            if llm_changed or emotion_changed or jobs_queue_changed:
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
                    image_understanding_service=self.image_understanding_service,
                    task_queue=self.task_queue,
                    window_preprocessor=self.window_preprocessor,
                    metrics=self.metrics,
                )
                rebuilt.append("chat_orchestrator")

            if scheduler_changed:
                # Stop old scheduler to avoid duplicate threads/jobs.
                self.periodic_scheduler.stop()
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
                rebuilt.append("periodic_scheduler")
                self.periodic_scheduler.start()

            return {
                "rebuilt": rebuilt,
                "emotion_changed": emotion_changed,
                "llm_changed": llm_changed,
                "jobs_queue_changed": jobs_queue_changed,
                "scheduler_changed": scheduler_changed,
            }


container = Container()
