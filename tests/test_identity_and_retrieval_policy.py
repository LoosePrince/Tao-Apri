from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.domain.models import Session
from app.domain.services.identity_service import IdentityService
from app.repos.in_memory import InMemorySessionRepo, InMemoryUserRepo
from app.services.llm_client import LLMClient


def test_session_kept_within_renew_window() -> None:
    user_repo = InMemoryUserRepo()
    session_repo = InMemorySessionRepo()
    service = IdentityService(user_repo, session_repo)
    user_id = "user-keep"
    old = Session(
        session_id="session-old",
        user_id=user_id,
        turn_count=7,
        last_seen_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    session_repo.upsert(old)
    original = settings.session.renew_after_hours
    try:
        settings.session.renew_after_hours = 3
        _, session = service.ensure_user_and_session(user_id)
        assert session.session_id == "session-old"
    finally:
        settings.session.renew_after_hours = original


def test_session_renewed_after_renew_window() -> None:
    user_repo = InMemoryUserRepo()
    session_repo = InMemorySessionRepo()
    service = IdentityService(user_repo, session_repo)
    user_id = "user-renew"
    old = Session(
        session_id="session-old",
        user_id=user_id,
        turn_count=9,
        last_seen_at=datetime.now(timezone.utc) - timedelta(hours=4),
    )
    session_repo.upsert(old)
    original = settings.session.renew_after_hours
    try:
        settings.session.renew_after_hours = 3
        _, session = service.ensure_user_and_session(user_id)
        assert session.session_id != "session-old"
        assert session.turn_count == 0
    finally:
        settings.session.renew_after_hours = original


def test_retrieval_plan_parser_handles_no_retrieval() -> None:
    plan = LLMClient._parse_retrieval_plan(
        '{"should_retrieve": false, "queries": [], "reason": "当前问题无需历史"}',
        user_message="今天天气不错",
    )
    assert plan.should_retrieve is False
    assert plan.queries == []


def test_retrieval_plan_parser_fallbacks_to_user_message() -> None:
    plan = LLMClient._parse_retrieval_plan(
        '{"should_retrieve": true, "queries": [], "reason": "需要历史"}',
        user_message="你还记得我上次说的吗",
    )
    assert plan.should_retrieve is True
    assert plan.queries == ["你还记得我上次说的吗"]
