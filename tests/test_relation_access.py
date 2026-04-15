from app.core.config import settings
from app.domain.models import UserPreference, UserRelation
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
from types import SimpleNamespace


class EchoMemoryLLMClient:
    def plan_retrieval(self, **kwargs) -> RetrievalPlan:  # noqa: ANN003
        user_message = kwargs["user_message"]
        return RetrievalPlan(should_retrieve=True, queries=[user_message], reason="test")

    def generate_reply(self, **kwargs) -> str:  # noqa: ANN003
        prompt_context = kwargs["prompt_context"]
        return prompt_context.memory_context

    def generate_profile_decision(self, **kwargs) -> dict[str, object]:  # noqa: ANN003
        return {
            "profile_summary": "近期关注话题：日常近况。",
            "preference_summary": "偏好信息有限，建议继续观察。",
            "preferred_address": "",
            "tone_preference": "自然中性",
            "schedule_state": "常规节奏",
            "fatigue_level": 0.0,
            "emotion_peak_level": 0.0,
        }

    def evolve_relation_decision(self, **kwargs) -> dict[str, object]:  # noqa: ANN003
        return {
            "polarity": "neutral",
            "strength": 0.4,
            "trust_score": 0.4,
            "intimacy_score": 0.4,
            "dependency_score": 0.2,
        }

    def summarize_group_emotion(self, **kwargs):  # noqa: ANN003
        return SimpleNamespace(score=0.0, text="群体情绪：中性平稳。")

    def decide_cross_access(self, **kwargs):  # noqa: ANN003
        memories = kwargs["memories"]
        allowed_ids: set[str] = set()
        relation_denied = 0
        preference_denied = 0
        for item in memories:
            relation = item.get("relation", {})
            preference = item.get("preference", {})
            if float(relation.get("strength", 0.0) or 0.0) < 0.2:
                relation_denied += 1
                continue
            if str(preference.get("share_default", "deny")) == "deny":
                preference_denied += 1
                continue
            allowed_ids.add(str(item["message_id"]))

        return SimpleNamespace(
            allowed_message_ids=allowed_ids,
            relation_denied=relation_denied,
            similarity_denied=0,
            preference_denied=preference_denied,
        )


def _build_orchestrator(db_path: str) -> tuple[ChatOrchestrator, MemoryWriter, SQLiteRelationRepo, SQLitePreferenceRepo]:
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
        llm_client=EchoMemoryLLMClient(),  # type: ignore[arg-type]
        task_queue=TaskQueue(enabled=False, worker_count=1, queue_size=100),
    )
    return orchestrator, memory_writer, relation_repo, preference_repo


def test_unrelated_user_gets_i_dont_know_cross_memory(tmp_path) -> None:
    old_min_score = settings.retrieval.min_score
    settings.retrieval.min_score = 0.0
    try:
        orchestrator, memory_writer, _, preference_repo = _build_orchestrator(str(tmp_path / "r1.db"))
        preference_repo.upsert(
            UserPreference(user_id="u_b", share_default="allow", topic_visibility={})
        )
        memory_writer.write(
            session_id="s_b",
            user_id="u_b",
            role="user",
            content="project workload pressure and overtime",
            emotion_score=0.0,
        )
        result = orchestrator.handle_message(user_id="u_a", user_message="project workload status")
        assert "我不知道" in result.reply
    finally:
        settings.retrieval.min_score = old_min_score


def test_positive_relation_can_access_topic_summary(tmp_path) -> None:
    old_min_score = settings.retrieval.min_score
    settings.retrieval.min_score = 0.0
    try:
        orchestrator, memory_writer, relation_repo, preference_repo = _build_orchestrator(str(tmp_path / "r2.db"))
        relation_repo.upsert(
            UserRelation(
                source_user_id="u_a",
                target_user_id="u_b",
                polarity="positive",
                strength=0.9,
                trust_score=0.8,
            )
        )
        preference_repo.upsert(
            UserPreference(user_id="u_b", share_default="allow", topic_visibility={})
        )
        memory_writer.write(
            session_id="s_b",
            user_id="u_b",
            role="user",
            content="project workload pressure and overtime",
            emotion_score=0.0,
        )
        result = orchestrator.handle_message(user_id="u_a", user_message="project workload status")
        assert "跨对话模糊参考" in result.reply
        assert "日常近况" in result.reply or "工作与职业" in result.reply
    finally:
        settings.retrieval.min_score = old_min_score


def test_positive_relation_retrieves_more_than_negative(tmp_path) -> None:
    old_min_score = settings.retrieval.min_score
    settings.retrieval.min_score = 0.0
    try:
        # positive relation
        orch_pos, writer_pos, rel_pos, pref_pos = _build_orchestrator(str(tmp_path / "r3_pos.db"))
        rel_pos.upsert(
            UserRelation(
                source_user_id="u_a",
                target_user_id="u_b",
                polarity="positive",
                strength=0.9,
                trust_score=0.8,
            )
        )
        pref_pos.upsert(UserPreference(user_id="u_b", share_default="allow", topic_visibility={}))
        writer_pos.write(
            session_id="s_b",
            user_id="u_b",
            role="user",
            content="project workload pressure and overtime",
            emotion_score=0.0,
        )
        res_pos = orch_pos.handle_message(user_id="u_a", user_message="project workload status")

        # negative relation
        orch_neg, writer_neg, rel_neg, pref_neg = _build_orchestrator(str(tmp_path / "r3_neg.db"))
        rel_neg.upsert(
            UserRelation(
                source_user_id="u_a",
                target_user_id="u_b",
                polarity="negative",
                strength=0.9,
                trust_score=0.2,
            )
        )
        pref_neg.upsert(UserPreference(user_id="u_b", share_default="allow", topic_visibility={}))
        writer_neg.write(
            session_id="s_b",
            user_id="u_b",
            role="user",
            content="project workload pressure and overtime",
            emotion_score=0.0,
        )
        res_neg = orch_neg.handle_message(user_id="u_a", user_message="project workload status")

        assert "跨对话模糊参考" in res_pos.reply
        assert "跨对话模糊参考" not in res_neg.reply
    finally:
        settings.retrieval.min_score = old_min_score


def test_preference_topic_deny_blocks_cross_retrieval(tmp_path) -> None:
    old_min_score = settings.retrieval.min_score
    settings.retrieval.min_score = 0.0
    try:
        orchestrator, memory_writer, relation_repo, preference_repo = _build_orchestrator(str(tmp_path / "r4.db"))
        relation_repo.upsert(
            UserRelation(
                source_user_id="u_a",
                target_user_id="u_b",
                polarity="positive",
                strength=0.9,
                trust_score=0.8,
            )
        )
        preference_repo.upsert(
            UserPreference(
                user_id="u_b",
                share_default="allow",
                topic_visibility={"工作与职业": "deny"},
            )
        )
        memory_writer.write(
            session_id="s_b",
            user_id="u_b",
            role="user",
            content="最近工作加班很多，项目压力很大",
            emotion_score=0.0,
        )
        result = orchestrator.handle_message(user_id="u_a", user_message="最近工作上都在聊什么")
        assert "跨对话模糊参考" not in result.reply
    finally:
        settings.retrieval.min_score = old_min_score
