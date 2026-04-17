from datetime import datetime, timezone

from app.core.config import settings
from app.domain.conversation_scope import ConversationScope
from app.domain.models import Message, UserPreference, UserRelation
from app.services.retrieval_policy_service import RetrievalPolicyService


class _RelationRepo:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], UserRelation] = {}

    def get(self, source_user_id: str, target_user_id: str) -> UserRelation | None:
        return self._items.get((source_user_id, target_user_id))

    def upsert(self, relation: UserRelation) -> None:
        self._items[(relation.source_user_id, relation.target_user_id)] = relation


class _PreferenceRepo:
    def __init__(self) -> None:
        self._items: dict[str, UserPreference] = {}

    def get(self, user_id: str) -> UserPreference | None:
        return self._items.get(user_id)

    def upsert(self, pref: UserPreference) -> None:
        self._items[pref.user_id] = pref


def _memory(other_user_id: str) -> Message:
    return Message(
        message_id="m1",
        user_id=other_user_id,
        role="user",
        raw_content="我们最近都在聊学习压力",
        sanitized_content="我们最近都在聊学习压力",
        created_at=datetime.now(timezone.utc),
        session_id="s1",
        emotion_score=0.0,
        related_user_ids=[],
        scene_type="group",
        group_id="g1",
        scope_id="group:qq:g1",
    )


def test_relation_access_min_strength_controls_cross_access() -> None:
    relation_repo = _RelationRepo()
    preference_repo = _PreferenceRepo()
    service = RetrievalPolicyService(relation_repo=relation_repo, preference_repo=preference_repo)
    viewer = ConversationScope.group(platform="qq", user_id="viewer", group_id="g1")
    memory = _memory("other")
    preference_repo.upsert(UserPreference(user_id="other", share_default="allow"))
    relation_repo.upsert(
        UserRelation(source_user_id="viewer", target_user_id="other", polarity="positive", strength=0.35, trust_score=0.8)
    )
    old_threshold = settings.retrieval.relation_access_min_strength
    try:
        settings.retrieval.relation_access_min_strength = 0.4
        decision = service.decide(viewer=viewer, memory=memory)
        assert decision.exposure == "deny"
        settings.retrieval.relation_access_min_strength = 0.2
        decision_relaxed = service.decide(viewer=viewer, memory=memory)
        assert decision_relaxed.exposure in {"summary", "redacted_snippet"}
    finally:
        settings.retrieval.relation_access_min_strength = old_threshold
