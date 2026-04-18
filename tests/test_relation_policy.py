from __future__ import annotations

from app.domain.models import UserRelation
from app.domain.relation_policy import (
    apply_numeric_and_tags_from_decision,
    compute_boundary_from_scores,
    ensure_developer_tag,
    finalize_relation_after_update,
    merge_boundary,
    normalize_relation_tags,
)


def test_merge_boundary_picks_stricter() -> None:
    assert merge_boundary("normal", "warn") == "warn"
    assert merge_boundary("warn", "normal") == "warn"
    assert merge_boundary("restricted", "warn") == "restricted"


def test_normalize_relation_tags_filters_unknown() -> None:
    assert normalize_relation_tags(["developer", "unknown", "FRIEND"], allowed=frozenset({"developer", "friend"})) == [
        "developer",
        "friend",
    ]


def test_compute_boundary_from_scores() -> None:
    assert compute_boundary_from_scores(polarity="neutral", trust_score=0.5, intimacy_score=0.5) == "normal"
    assert compute_boundary_from_scores(polarity="neutral", trust_score=0.1, intimacy_score=0.0) == "restricted"
    assert compute_boundary_from_scores(polarity="negative", trust_score=0.3, intimacy_score=0.0) in ("warn", "restricted")


def test_ensure_developer_tag_appends(monkeypatch) -> None:
    from app.core import config

    settings = config.settings
    old = settings.relation.developer_user_ids
    try:
        settings.relation.developer_user_ids = ["u_dev"]
        r = UserRelation(source_user_id="u_dev", target_user_id="assistant")
        ensure_developer_tag(r, user_id="u_dev")
        assert "developer" in r.relation_tags
    finally:
        settings.relation.developer_user_ids = old


def test_finalize_clamps_boundary_with_rules() -> None:
    from app.core import config

    settings = config.settings
    r = UserRelation(
        source_user_id="u",
        target_user_id="assistant",
        polarity="neutral",
        trust_score=0.05,
        intimacy_score=0.0,
        relation_tags=["neutral"],
        role_priority="neutral",
        boundary_state="normal",
    )
    finalize_relation_after_update(r, user_id="u")
    assert r.boundary_state == "restricted"


def test_sqlite_user_relation_roundtrip(tmp_path) -> None:
    from app.domain.models import UserRelation
    from app.repos.sqlite_repo import SQLiteRelationRepo, SQLiteStore

    db = tmp_path / "rel.db"
    store = SQLiteStore(str(db))
    repo = SQLiteRelationRepo(store)
    r = UserRelation(
        source_user_id="a",
        target_user_id="assistant",
        polarity="positive",
        strength=0.5,
        trust_score=0.6,
        intimacy_score=0.4,
        dependency_score=0.1,
        relation_tags=["friend"],
        role_priority="friend",
        boundary_state="normal",
    )
    repo.upsert(r)
    got = repo.get("a", "assistant")
    assert got is not None
    assert got.relation_tags == ["friend"]
    assert got.role_priority == "friend"
    assert got.boundary_state == "normal"


def test_apply_numeric_and_tags_from_decision_partial() -> None:
    r = UserRelation(
        source_user_id="u",
        target_user_id="assistant",
        trust_score=0.5,
        relation_tags=["friend"],
        role_priority="friend",
        boundary_state="normal",
    )
    apply_numeric_and_tags_from_decision(
        r,
        {"trust_score": 0.2, "relation_tags": ["friend", "strained"], "boundary_state": "warn"},
    )
    assert r.trust_score == 0.2
    assert "strained" in r.relation_tags
    assert r.boundary_state == "warn"
