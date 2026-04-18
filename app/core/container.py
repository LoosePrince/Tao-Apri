import json
import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from app.core.config import settings
from app.core.metrics import MetricsRegistry
from app.domain.conversation_scope import ConversationScope
from app.domain.services.emotion_engine import EmotionEngine
from app.domain.services.identity_service import IdentityService
from app.domain.services.memory_writer import MemoryWriter
from app.domain.services.persona_engine import PersonaEngine
from app.jobs.delayed_task_scheduler import DelayedTaskScheduler
from app.jobs.emotion_aggregator import EmotionAggregatorJob
from app.jobs.periodic_scheduler import PeriodicScheduler
from app.jobs.task_queue import TaskQueue
from app.repos.in_memory import InMemoryVectorRepo
from app.repos.sqlite_repo import (SQLiteDelayedTaskRepo,
                                   SQLiteEmotionStateRepo, SQLiteFactRepo,
                                   SQLiteMessageRepo, SQLitePreferenceRepo,
                                   SQLiteProfileRepo, SQLiteRelationRepo,
                                   SQLiteSessionRepo, SQLiteStore,
                                   SQLiteUserRepo, SQLiteVectorRepo)
from app.services.channel_sender import ChannelRouter
from app.services.chat_orchestrator import ChatOrchestrator
from app.services.conversation_window_manager import ConversationWindowManager
from app.services.image_understanding_service import ImageUnderstandingService
from app.services.llm_client import LLMClient
from app.services.prompt_composer import PromptComposer
from app.services.retrieval_policy_service import RetrievalPolicyService
from app.services.window_preprocessor import WindowPreprocessor
from app.tool_runtime.audit import SendRateLimiter
from app.tool_runtime.builtin_tools import (CancelDelayedTaskTool,
                                            QueryDelayedTasksTool,
                                            QueryMessagesTool,
                                            ScheduleDelayedTaskTool,
                                            SearchMemoryTool, SendMessageTool)
from app.tool_runtime.registry import ToolRegistry
from app.tool_runtime.runtime import ToolRuntime

if TYPE_CHECKING:
    from app.core.config import Settings


logger = logging.getLogger(__name__)


class Container:
    def _build_tool_runtime(self, scope: ConversationScope) -> ToolRuntime:
        registry = ToolRegistry()
        registry.register(
            SearchMemoryTool(
                vector_repo=self.vector_repo,
                retrieval_policy_service=self.retrieval_policy_service,
                viewer_scope=scope,
            )
        )
        registry.register(QueryMessagesTool(message_repo=self.message_repo))
        registry.register(
            SendMessageTool(
                router=self.channel_router,
                rate_limiter=self.send_rate_limiter,
            )
        )
        registry.register(
            ScheduleDelayedTaskTool(
                delayed_task_repo=self.delayed_task_repo,
                viewer_scope=scope,
            )
        )
        registry.register(
            QueryDelayedTasksTool(
                delayed_task_repo=self.delayed_task_repo,
                viewer_scope=scope,
            )
        )
        registry.register(
            CancelDelayedTaskTool(
                delayed_task_repo=self.delayed_task_repo,
                viewer_scope=scope,
            )
        )
        return ToolRuntime(llm_client=self.llm_client, registry=registry, metrics=self.metrics)

    def register_channel_sender(self, channel: str, sender: object) -> None:
        self.channel_router.register(channel, sender)  # type: ignore[arg-type]

    @staticmethod
    def _resolve_sqlite_db_path() -> str:
        sqlite_path = (settings.storage.sqlite_db_path or "").strip()
        if sqlite_path and sqlite_path != "social_persona_ai.db":
            return sqlite_path
        postgres_dsn = (settings.storage.postgres_dsn or "").strip()
        if not postgres_dsn:
            return sqlite_path or "social_persona_ai.db"
        parsed = urlparse(postgres_dsn)
        db_name = (parsed.path or "").strip("/ ")
        if db_name:
            return f"{db_name}.db"
        return sqlite_path or "social_persona_ai.db"

    def __init__(self) -> None:
        import threading

        self._lock = threading.RLock()
        # Set from app lifespan after OneBot WS is up; required to push delayed-task replies to QQ.
        self.onebot_service: Any = None
        self.store = SQLiteStore(self._resolve_sqlite_db_path())
        self.user_repo = SQLiteUserRepo(self.store)
        self.session_repo = SQLiteSessionRepo(self.store)
        self.message_repo = SQLiteMessageRepo(self.store)
        self.fact_repo = SQLiteFactRepo(self.store)
        vector_dsn = (settings.storage.vector_dsn or "").strip().lower()
        if vector_dsn.startswith("memory://"):
            self.vector_repo = InMemoryVectorRepo()
        else:
            self.vector_repo = SQLiteVectorRepo(self.store)
        self.emotion_state_repo = SQLiteEmotionStateRepo(self.store)
        self.relation_repo = SQLiteRelationRepo(self.store)
        self.preference_repo = SQLitePreferenceRepo(self.store)
        self.profile_repo = SQLiteProfileRepo(self.store)
        self.delayed_task_repo = SQLiteDelayedTaskRepo(self.store)

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
        self.channel_router = ChannelRouter()
        self.send_rate_limiter = SendRateLimiter(limit_per_minute=settings.tools.send_rate_limit_per_minute)
        self.retrieval_policy_service = RetrievalPolicyService(
            relation_repo=self.relation_repo,
            preference_repo=self.preference_repo,
        )
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
        self.delayed_task_scheduler = DelayedTaskScheduler(
            repo=self.delayed_task_repo,
            task_queue=self.task_queue,
            executor=self._execute_delayed_task,
            metrics=self.metrics,
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
            retrieval_policy_service=self.retrieval_policy_service,
            tool_runtime_factory=self._build_tool_runtime,
        )
        self.window_manager = ConversationWindowManager(
            batch_executor=lambda scope, batch, abort, nickname, source_message_id, attachments, group_hints, window_round_id: self.chat_orchestrator.handle_window_batch(
                scope=scope,
                user_messages=batch,
                abort_requested=abort,
                nickname=nickname,
                source_message_id=source_message_id,
                attachments=attachments,
                group_hints=group_hints,
                window_round_id=window_round_id,
            ),
            metrics=self.metrics,
        )

    def _execute_delayed_task(self, task) -> None:
        from app.domain.models import DelayedTask

        if not isinstance(task, DelayedTask):
            raise TypeError(f"unexpected task type: {type(task)}")
        payload = json.loads(task.payload_json or "{}")
        message = str(payload.get("message", "")).strip()
        if not message:
            message = task.description.strip() or "执行延时任务"
        trigger_source = str(payload.get("trigger_source", task.trigger_source)).strip() or "schedule_delayed_task"
        reason = str(payload.get("reason", task.reason)).strip() or "未说明"
        platform = str(payload.get("platform", "tool_runtime")).strip() or "tool_runtime"
        scene_type = str(payload.get("scene_type", "private")).strip().lower()
        user_id = str(payload.get("user_id", "")).strip()
        group_id = str(payload.get("group_id", "")).strip()
        if not user_id:
            raise ValueError("delayed task payload missing user_id")
        if scene_type == "group" and group_id:
            scope = ConversationScope.group(platform=platform, group_id=group_id, user_id=user_id)
        else:
            scope = ConversationScope.private(platform=platform, user_id=user_id)
        synthetic_message = (
            f"[延时任务触发]\n任务ID: {task.task_id}\n触发源: {trigger_source}\n原因: {reason}\n"
            f"任务描述: {task.description}\n执行内容: {message}"
        )
        result = self.chat_orchestrator.handle_window_batch(
            scope=scope,
            user_messages=[synthetic_message],
            abort_requested=False,
            nickname=str(payload.get("nickname", "")).strip() or None,
            source_message_id=f"delayed:{task.task_id}",
            attachments=[],
            group_hints=None,
            window_round_id=None,
        )
        self._send_delayed_task_reply_to_onebot(scope=scope, reply=result.reply, task_id=task.task_id)

    def _send_delayed_task_reply_to_onebot(
        self,
        *,
        scope: ConversationScope,
        reply: str,
        task_id: str,
    ) -> None:
        """Delayed tasks bypass ConversationWindowManager, so we must emit the outbound separately."""
        text = (reply or "").strip()
        if not text:
            return
        ob = self.onebot_service
        if ob is None or not settings.onebot.enabled:
            logger.warning(
                "Delayed task reply not pushed to OneBot (service missing or OneBot disabled) | task_id=%s | scope=%s",
                task_id,
                scope.scope_id,
            )
            return
        try:
            if scope.scene_type == "group" and scope.group_id:
                ob.send_message_sync(target_type="group", target_id=str(scope.group_id), content=text)
            else:
                ob.send_message_sync(target_type="private", target_id=str(scope.actor_user_id), content=text)
            logger.info(
                "Delayed task reply pushed to OneBot | task_id=%s | scope=%s | len=%s",
                task_id,
                scope.scope_id,
                len(text),
            )
        except Exception as exc:  # pragma: no cover - network / runtime loop
            logger.exception(
                "Delayed task OneBot send failed | task_id=%s | scope=%s | err=%s",
                task_id,
                scope.scope_id,
                exc,
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
            delayed_task_changed = old_dump.get("delayed_task") != new_dump.get("delayed_task")

            # 先更新全局 settings，让后续重建读到新值。
            for top_key in SettingsModel.model_fields.keys():
                setattr(settings, top_key, getattr(new_settings, top_key))

            rebuilt: list[str] = []

            if llm_changed:
                self.llm_client = LLMClient()
                self.image_understanding_service = ImageUnderstandingService(llm_client=self.llm_client)
                self.window_preprocessor = WindowPreprocessor(llm_client=self.llm_client)
                self.send_rate_limiter = SendRateLimiter(limit_per_minute=settings.tools.send_rate_limit_per_minute)
                rebuilt.append("llm_client")
                rebuilt.append("image_understanding_service")
                rebuilt.append("window_preprocessor")
                rebuilt.append("send_rate_limiter")

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
                    retrieval_policy_service=self.retrieval_policy_service,
                    tool_runtime_factory=self._build_tool_runtime,
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

            if delayed_task_changed or jobs_queue_changed:
                self.delayed_task_scheduler.stop()
                self.delayed_task_scheduler = DelayedTaskScheduler(
                    repo=self.delayed_task_repo,
                    task_queue=self.task_queue,
                    executor=self._execute_delayed_task,
                    metrics=self.metrics,
                )
                self.delayed_task_scheduler.start()
                rebuilt.append("delayed_task_scheduler")

            return {
                "rebuilt": rebuilt,
                "emotion_changed": emotion_changed,
                "llm_changed": llm_changed,
                "jobs_queue_changed": jobs_queue_changed,
                "scheduler_changed": scheduler_changed,
                "delayed_task_changed": delayed_task_changed,
            }


container = Container()
