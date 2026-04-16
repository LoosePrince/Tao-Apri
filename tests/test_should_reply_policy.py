from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from app.core.metrics import MetricsRegistry
from app.domain.services.emotion_engine import EmotionEngine
from app.domain.services.identity_service import IdentityService
from app.domain.services.memory_writer import MemoryWriter
from app.domain.services.persona_engine import PersonaEngine
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
from app.services.llm_client import RetrievalPlan
from app.services.prompt_composer import PromptComposer
from app.services.window_preprocessor import WindowPreprocessor
import pytest


@dataclass(slots=True)
class _ShouldReplyDecision:
    should_reply: bool
    reason: str = ""


class _ShouldReplyFalseLLMClient:
    def __init__(self) -> None:
        self.generate_reply_called = False

    def plan_retrieval(self, **kwargs) -> RetrievalPlan:  # noqa: ANN003
        return RetrievalPlan(should_retrieve=False, queries=[], reason="test:skip_retrieval")

    def generate_reply(self, **kwargs) -> str:  # noqa: ANN003
        self.generate_reply_called = True
        raise AssertionError("generate_reply should not be called when should_reply=false")

    def generate_profile_decision(self, **kwargs) -> dict[str, object]:  # noqa: ANN003
        return {
            "profile_summary": "近期表达仍在观察中。",
            "preference_summary": "偏好信息已记录。",
            "preferred_address": "",
            "tone_preference": "自然中性",
            "schedule_state": "常规节奏",
            "fatigue_level": 0.3,
            "emotion_peak_level": 0.2,
        }

    def evolve_relation_decision(self, **kwargs) -> dict[str, object]:  # noqa: ANN003
        return {
            "polarity": "neutral",
            "strength": 0.0,
            "trust_score": 0.0,
            "intimacy_score": 0.0,
            "dependency_score": 0.0,
        }

    def summarize_group_emotion(self, **kwargs):  # noqa: ANN003
        return SimpleNamespace(score=0.0, text="群体情绪：中性平稳。")

    def decide_cross_access(self, **kwargs):  # noqa: ANN003
        return SimpleNamespace(
            allowed_message_ids=set(),
            relation_denied=0,
            similarity_denied=0,
            preference_denied=0,
        )

    def extract_keywords(self, **kwargs) -> list[str]:  # noqa: ANN003
        return []

    def summarize_long_message(self, **kwargs) -> str:  # noqa: ANN003
        return "..."

    def summarize_window_messages(self, **kwargs) -> str:  # noqa: ANN003
        return "窗口摘要"

    def decide_should_reply(self, **kwargs) -> _ShouldReplyDecision:  # noqa: ANN003
        return _ShouldReplyDecision(should_reply=False, reason="test_policy_no_reply")


def _build_orchestrator(db_path: str):  # noqa: ANN001
    store = SQLiteStore(db_path)
    user_repo = SQLiteUserRepo(store)
    session_repo = SQLiteSessionRepo(store)
    message_repo = SQLiteMessageRepo(store)
    fact_repo = SQLiteFactRepo(store)
    vector_repo = SQLiteVectorRepo(store)
    emotion_state_repo = SQLiteEmotionStateRepo(store)
    relation_repo = SQLiteRelationRepo(store)
    preference_repo = SQLitePreferenceRepo(store)
    profile_repo = SQLiteProfileRepo(store)

    identity_service = IdentityService(user_repo, session_repo)
    memory_writer = MemoryWriter(message_repo=message_repo, vector_repo=vector_repo, fact_repo=fact_repo)

    llm_client = _ShouldReplyFalseLLMClient()
    orchestrator = ChatOrchestrator(
        identity_service=identity_service,
        persona_engine=PersonaEngine(),
        emotion_engine=EmotionEngine(state_repo=emotion_state_repo),
        message_repo=message_repo,
        vector_repo=vector_repo,
        relation_repo=relation_repo,
        preference_repo=preference_repo,
        profile_repo=profile_repo,
        memory_writer=memory_writer,
        prompt_composer=PromptComposer(),
        llm_client=llm_client,  # type: ignore[arg-type]
        task_queue=TaskQueue(enabled=False, worker_count=1, queue_size=100),
        window_preprocessor=WindowPreprocessor(llm_client=llm_client),  # type: ignore[arg-type]
        metrics=MetricsRegistry(),
    )
    return orchestrator, llm_client, memory_writer, message_repo, session_repo, relation_repo, orchestrator.metrics


def test_should_reply_false_skips_assistant_persistence_and_turn_count(tmp_path) -> None:
    orchestrator, llm_client, _, message_repo, session_repo, relation_repo, metrics = _build_orchestrator(
        str(tmp_path / "should_reply_false.db")
    )

    result = orchestrator.handle_message(user_id="u_a", user_message="你好")
    assert result.reply == ""
    assert not llm_client.generate_reply_called

    messages = message_repo.list_by_user("u_a", limit=20)
    roles = [m.role for m in messages]
    assert roles == ["user"]

    session = session_repo.get_by_user_id("u_a")
    assert session is not None
    assert session.turn_count == 0

    relation = relation_repo.get("u_a", "assistant")
    assert relation is None

    snapshot = metrics.snapshot()
    assert int(snapshot.get("reply_skipped_count", 0)) == 1

