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
    orchestrator.handle_message(user_id="u_profile", user_message="我最近学习压力有点大，叫我阿林")
    orchestrator.handle_message(user_id="u_profile", user_message="我喜欢晚上复习，也不想被打扰，聊天随意点")
    profile = profile_repo.get("u_profile")
    assert profile is not None
    assert "学习与考试" in profile.profile_summary
    assert profile.preference_summary.strip() != ""
    assert profile.preferred_address == "阿林"
    assert profile.tone_preference == "偏轻松口语"
    assert profile.schedule_state.strip() != ""
    assert 0.0 <= profile.fatigue_level <= 1.0
    assert 0.0 <= profile.emotion_peak_level <= 1.0


def test_user_ai_relation_state_evolves_after_conversation(tmp_path) -> None:
    orchestrator, _, relation_repo = _build_orchestrator(str(tmp_path / "relation_state.db"))
    orchestrator.handle_message(user_id="u_rel", user_message="我最近状态不好，你能给我建议吗")
    first = relation_repo.get("u_rel", "assistant")
    orchestrator.handle_message(user_id="u_rel", user_message="这件事只能靠你告诉我怎么办")
    second = relation_repo.get("u_rel", "assistant")
    assert first is not None
    relation = relation_repo.get("u_rel", "assistant")
    assert relation is not None
    assert relation.intimacy_score > 0.0
    assert relation.trust_score > 0.0
    assert relation.dependency_score > 0.0
    assert relation.strength > 0.0
    assert second is not None
    assert second.strength > first.strength
    assert second.dependency_score >= first.dependency_score


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
    assert short_reply != long_reply


def test_group_emotion_context_injected_into_profile_context(tmp_path) -> None:
    store = SQLiteStore(str(tmp_path / "group_emotion.db"))
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
    memory_writer.write(
        session_id="s_other",
        user_id="u_other",
        role="user",
        content="今天太开心了",
        emotion_score=0.9,
    )
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
    reply = orchestrator.handle_message(user_id="u_viewer", user_message="我最近在复习").reply
    assert "群体情绪：整体偏积极" in reply


def test_long_term_memory_affects_address_and_tone_context(tmp_path) -> None:
    store = SQLiteStore(str(tmp_path / "long_term_tone.db"))
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
    orchestrator.handle_message(user_id="u_mem", user_message="叫我小北，正式一点")
    reply = orchestrator.handle_message(user_id="u_mem", user_message="今天聊聊状态").reply
    assert "称呼偏好：优先称呼用户为“小北”" in reply
    assert "语气偏好：偏正式克制" in reply
