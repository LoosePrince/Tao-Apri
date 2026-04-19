from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.domain.models import Message
from app.services.history_reference_builder import build_history_reference_context


def test_history_reference_includes_absolute_and_relative_lines() -> None:
    now = datetime(2026, 4, 19, 15, 0, 0, tzinfo=timezone.utc)
    past = now - timedelta(hours=2, minutes=5)
    messages = [
        Message(
            message_id="m1",
            user_id="u1",
            role="user",
            raw_content="早",
            sanitized_content="早",
            created_at=past,
            session_id="s1",
            scope_id="private:test:u1",
        ),
    ]
    text = build_history_reference_context(now=now, messages=messages)
    assert "[绝对时间]" in text
    assert "[相对本轮]" in text
    assert "距今" in text
    assert "早" in text
    assert "低权重" in text


def test_history_reference_empty_messages() -> None:
    now = datetime(2026, 4, 19, 12, 0, 0, tzinfo=timezone.utc)
    text = build_history_reference_context(now=now, messages=[])
    assert "尚无更早" in text


def test_sqlite_list_by_scope_returns_chronological_slice(tmp_path) -> None:
    from app.repos.sqlite_repo import SQLiteMessageRepo, SQLiteStore

    store = SQLiteStore(str(tmp_path / "hist.db"))
    repo = SQLiteMessageRepo(store)
    scope = "private:api:u9"
    t0 = datetime(2026, 4, 19, 10, 0, 0, tzinfo=timezone.utc)
    for i, off in enumerate([0, 1, 2]):
        repo.add(
            Message(
                message_id=f"id{i}",
                user_id="u9",
                role="user",
                raw_content=str(i),
                sanitized_content=str(i),
                created_at=t0 + timedelta(minutes=off),
                session_id="s",
                scope_id=scope,
            )
        )
    rows = repo.list_by_scope(scope, limit=2)
    assert [m.sanitized_content for m in rows] == ["1", "2"]
