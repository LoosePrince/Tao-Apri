from app.domain.services.emotion_engine import EmotionEngine
from app.domain.services.identity_service import IdentityService
from app.domain.services.memory_writer import MemoryWriter
from app.domain.services.persona_engine import PersonaEngine
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


class EchoLLMClient:
    def plan_retrieval(self, **kwargs) -> RetrievalPlan:  # noqa: ANN003
        user_message = kwargs["user_message"]
        return RetrievalPlan(should_retrieve=True, queries=[user_message], reason="test")

    def generate_reply(self, **kwargs) -> str:  # noqa: ANN003
        return "ok"


class ProfileEchoLLMClient:
    def plan_retrieval(self, **kwargs) -> RetrievalPlan:  # noqa: ANN003
        user_message = kwargs["user_message"]
        return RetrievalPlan(should_retrieve=True, queries=[user_message], reason="test")

    def generate_reply(self, **kwargs) -> str:  # noqa: ANN003
        prompt_context = kwargs["prompt_context"]
        return prompt_context.profile_context


def _build_orchestrator(db_path: str) -> tuple[ChatOrchestrator, SQLiteProfileRepo, SQLiteRelationRepo]:
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
        llm_client=EchoLLMClient(),  # type: ignore[arg-type]
    )
    return orchestrator, profile_repo, relation_repo


def test_profile_generated_and_persisted_after_messages(tmp_path) -> None:
    orchestrator, profile_repo, _ = _build_orchestrator(str(tmp_path / "profile.db"))
    orchestrator.handle_message(user_id="u_profile", user_message="我最近学习压力有点大")
    orchestrator.handle_message(user_id="u_profile", user_message="我喜欢晚上复习，也不想被打扰")
    profile = profile_repo.get("u_profile")
    assert profile is not None
    assert "学习与考试" in profile.profile_summary
    assert profile.preference_summary.strip() != ""


def test_user_ai_relation_state_evolves_after_conversation(tmp_path) -> None:
    orchestrator, _, relation_repo = _build_orchestrator(str(tmp_path / "relation_state.db"))
    orchestrator.handle_message(user_id="u_rel", user_message="我最近状态不好，你能给我建议吗")
    orchestrator.handle_message(user_id="u_rel", user_message="这件事只能靠你告诉我怎么办")
    relation = relation_repo.get("u_rel", "assistant")
    assert relation is not None
    assert relation.intimacy_score > 0.0
    assert relation.trust_score > 0.0
    assert relation.dependency_score > 0.0
    assert relation.strength > 0.0


def test_profile_context_differs_by_user_expression_style(tmp_path) -> None:
    store = SQLiteStore(str(tmp_path / "profile_style.db"))
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
        llm_client=ProfileEchoLLMClient(),  # type: ignore[arg-type]
    )
    short_reply = orchestrator.handle_message(user_id="u_short", user_message="好困").reply
    long_reply = orchestrator.handle_message(
        user_id="u_long",
        user_message="今天我把项目里三处问题都复盘了，准备明天和同事同步具体的改进计划。",
    ).reply
    assert "表达更简短直接" in short_reply
    assert "表达相对完整，愿意展开描述" in long_reply
