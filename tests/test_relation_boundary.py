from __future__ import annotations

from app.core.config import settings
from app.domain.models import UserRelation
from app.services.relation_boundary import evaluate_relation_boundary


def test_boundary_developer_gets_extra_tone_line() -> None:
    r = UserRelation(
        source_user_id="u1",
        target_user_id="assistant",
        polarity="positive",
        trust_score=0.8,
        intimacy_score=0.5,
        relation_tags=["developer"],
        role_priority="developer",
        boundary_state="normal",
    )
    sig = evaluate_relation_boundary(
        r,
        user_message="hi",
        scene_type="private",
        group_bot_mentioned=False,
    )
    assert "开发者" in sig.tone_constraints
    assert sig.effective_boundary == "normal"


def test_group_restricted_skip_when_enabled(monkeypatch) -> None:
    r = UserRelation(
        source_user_id="u1",
        target_user_id="assistant",
        polarity="negative",
        trust_score=0.05,
        intimacy_score=0.0,
        relation_tags=[],
        role_priority="neutral",
        boundary_state="restricted",
    )
    monkeypatch.setattr(settings.relation, "group_skip_when_restricted_without_mention", True)
    sig = evaluate_relation_boundary(
        r,
        user_message="hi",
        scene_type="group",
        group_bot_mentioned=False,
    )
    assert sig.should_reply_override is False
    assert "relation_boundary" in sig.skip_reason_if_override
