from __future__ import annotations

from app.core.config import settings
from app.domain.conversation_scope import ConversationScope
from app.domain.models import Message, UserPreference, UserRelation
from app.domain.services.identity_service import IdentityService
from app.repos.sqlite_repo import (
    SQLitePreferenceRepo,
    SQLiteRelationRepo,
    SQLiteSessionRepo,
    SQLiteStore,
    SQLiteUserRepo,
)
from app.services.retrieval_policy_service import RetrievalPolicyService
from datetime import datetime, timezone


def test_identity_service_creates_independent_sessions_per_scope(tmp_path) -> None:
    store = SQLiteStore(str(tmp_path / "scope_sessions.db"))
    user_repo = SQLiteUserRepo(store)
    session_repo = SQLiteSessionRepo(store)
    service = IdentityService(user_repo=user_repo, session_repo=session_repo)

    s1 = service.ensure_user_and_session(
        ConversationScope.group(platform="onebot", group_id="100", user_id="u1")
    )[1]
    s2 = service.ensure_user_and_session(
        ConversationScope.group(platform="onebot", group_id="200", user_id="u1")
    )[1]
    s3 = service.ensure_user_and_session(ConversationScope.private(platform="api", user_id="u1"))[1]

    assert s1.session_id != s2.session_id
    assert s1.session_id != s3.session_id
    assert session_repo.get_by_scope_id("group:100:user:u1") is not None
    assert session_repo.get_by_scope_id("group:200:user:u1") is not None
    assert session_repo.get_by_scope_id("private:u1") is not None


def test_retrieval_policy_same_group_high_trust_allows_redacted_snippet(tmp_path) -> None:
    store = SQLiteStore(str(tmp_path / "policy_same_group.db"))
    relation_repo = SQLiteRelationRepo(store)
    pref_repo = SQLitePreferenceRepo(store)
    service = RetrievalPolicyService(relation_repo=relation_repo, preference_repo=pref_repo)

    relation_repo.upsert(
        UserRelation(source_user_id="u_a", target_user_id="u_b", polarity="positive", strength=0.9, trust_score=0.7)
    )
    pref_repo.upsert(UserPreference(user_id="u_b", share_default="allow", topic_visibility={}))

    viewer = ConversationScope.group(platform="onebot", group_id="100", user_id="u_a")
    mem = Message(
        message_id="m1",
        user_id="u_b",
        role="user",
        raw_content="x",
        sanitized_content="项目加班进度",
        created_at=datetime.now(timezone.utc),
        session_id="s_b",
        scope_id="group:100:user:u_b",
        scene_type="group",
        group_id="100",
        platform="onebot",
    )
    decision = service.decide(viewer=viewer, memory=mem)
    assert decision.exposure == "redacted_snippet"


def test_retrieval_policy_cross_group_denied_by_default(tmp_path) -> None:
    store = SQLiteStore(str(tmp_path / "policy_cross_group.db"))
    relation_repo = SQLiteRelationRepo(store)
    pref_repo = SQLitePreferenceRepo(store)
    service = RetrievalPolicyService(relation_repo=relation_repo, preference_repo=pref_repo)

    relation_repo.upsert(
        UserRelation(source_user_id="u_a", target_user_id="u_b", polarity="positive", strength=0.8, trust_score=0.7)
    )
    pref_repo.upsert(UserPreference(user_id="u_b", share_default="allow", topic_visibility={}))

    viewer = ConversationScope.group(platform="onebot", group_id="100", user_id="u_a")
    mem = Message(
        message_id="m2",
        user_id="u_b",
        role="user",
        raw_content="x",
        sanitized_content="项目加班进度",
        created_at=datetime.now(timezone.utc),
        session_id="s_b",
        scope_id="group:200:user:u_b",
        scene_type="group",
        group_id="200",
        platform="onebot",
    )
    decision = service.decide(viewer=viewer, memory=mem)
    assert decision.exposure == "deny"


def test_retrieval_policy_topic_deny_overrides_relation(tmp_path) -> None:
    store = SQLiteStore(str(tmp_path / "policy_topic_deny.db"))
    relation_repo = SQLiteRelationRepo(store)
    pref_repo = SQLitePreferenceRepo(store)
    service = RetrievalPolicyService(relation_repo=relation_repo, preference_repo=pref_repo)

    relation_repo.upsert(
        UserRelation(source_user_id="u_a", target_user_id="u_b", polarity="positive", strength=0.95, trust_score=0.9)
    )
    pref_repo.upsert(
        UserPreference(user_id="u_b", share_default="allow", topic_visibility={"工作与职业": "deny"})
    )

    viewer = ConversationScope.private(platform="api", user_id="u_a")
    mem = Message(
        message_id="m3",
        user_id="u_b",
        role="user",
        raw_content="x",
        sanitized_content="项目加班进度",
        created_at=datetime.now(timezone.utc),
        session_id="s_b",
        scope_id="private:u_b",
        scene_type="private",
        group_id=None,
        platform="api",
    )
    decision = service.decide(viewer=viewer, memory=mem)
    assert decision.exposure == "deny"


def test_onebot_group_whitelist_allows_without_mention(monkeypatch) -> None:
    import asyncio

    from app.integrations.onebot_ws_client import OneBotWSClient

    class _StubWS:
        async def send(self, _: str) -> None:
            return None

    class _Inspectable(OneBotWSClient):
        def __init__(self) -> None:
            super().__init__(window_manager=None)  # type: ignore[arg-type]
            self.scopes: list[str] = []

        async def _process_message(  # type: ignore[override]
            self,
            ws,
            *,
            scope: ConversationScope,
            user_text: str,
            nickname: str | None = None,
            group_bot_mentioned: bool | None = None,
            group_allow_autonomous: bool | None = None,
        ) -> None:
            del ws, user_text, nickname, group_bot_mentioned, group_allow_autonomous
            self.scopes.append(scope.scope_id)

    client = _Inspectable()
    old = list(settings.onebot.group_autonomous_whitelist)
    settings.onebot.group_autonomous_whitelist = [10001]
    try:
        async def _run() -> None:
            event = {
                "post_type": "message",
                "message_type": "group",
                "self_id": 3396584245,
                "group_id": 10001,
                    "user_id": settings.onebot.debug_only_user_id,
                "message_id": 7,
                    "sender": {"user_id": settings.onebot.debug_only_user_id},
                "message": [{"type": "text", "data": {"text": "不@也要处理"}}],
            }
            await client._handle_event(_StubWS(), event)
            await asyncio.sleep(0)

        asyncio.run(_run())
        assert client.scopes == [f"group:10001:user:{settings.onebot.debug_only_user_id}"]
    finally:
        settings.onebot.group_autonomous_whitelist = old

