from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.domain.models import Message
from app.services.history_reference_builder import (
    build_history_reference_context,
    merge_scope_and_cross_messages,
)


def test_history_reference_includes_absolute_and_relative_lines() -> None:
    now = datetime(2026, 4, 19, 15, 0, 0, tzinfo=timezone.utc)
    past = now - timedelta(hours=2, minutes=5)
    scope = "private:test:u1"
    messages = [
        Message(
            message_id="m1",
            user_id="u1",
            role="user",
            raw_content="早",
            sanitized_content="早",
            created_at=past,
            session_id="s1",
            scope_id=scope,
        ),
    ]
    text = build_history_reference_context(now=now, messages=messages, current_scope_id=scope)
    assert "[绝对时间]" in text
    assert "[相对本轮]" in text
    assert "距今" in text
    assert "早" in text
    assert "低权重" in text
    assert "来源=本会话" in text


def test_history_reference_empty_messages() -> None:
    now = datetime(2026, 4, 19, 12, 0, 0, tzinfo=timezone.utc)
    text = build_history_reference_context(now=now, messages=[], current_scope_id="private:x")
    assert "尚无更早" in text


def test_merge_interleaves_by_time() -> None:
    t0 = datetime(2026, 4, 19, 10, 0, 0, tzinfo=timezone.utc)
    a = Message(
        message_id="a1",
        user_id="u",
        role="user",
        raw_content="a1",
        sanitized_content="a1",
        created_at=t0,
        session_id="s",
        scope_id="scope-a",
    )
    b = Message(
        message_id="b1",
        user_id="u",
        role="user",
        raw_content="b1",
        sanitized_content="b1",
        created_at=t0 + timedelta(minutes=1),
        session_id="s",
        scope_id="scope-b",
    )
    c = Message(
        message_id="a2",
        user_id="u",
        role="user",
        raw_content="a2",
        sanitized_content="a2",
        created_at=t0 + timedelta(minutes=2),
        session_id="s",
        scope_id="scope-a",
    )
    merged = merge_scope_and_cross_messages([a, c], [b])
    assert [m.message_id for m in merged] == ["a1", "b1", "a2"]


def test_cross_mix_header_when_other_scope_present() -> None:
    now = datetime(2026, 4, 19, 18, 0, 0, tzinfo=timezone.utc)
    cur = "private:main:u1"
    other = "private:other:u1"
    messages = [
        Message(
            message_id="x1",
            user_id="u1",
            role="user",
            raw_content="old",
            sanitized_content="old",
            created_at=now - timedelta(hours=1),
            session_id="s",
            scope_id=cur,
        ),
        Message(
            message_id="x2",
            user_id="u1",
            role="user",
            raw_content="mix",
            sanitized_content="mix",
            created_at=now - timedelta(minutes=30),
            session_id="s",
            scope_id=other,
        ),
    ]
    text = build_history_reference_context(now=now, messages=messages, current_scope_id=cur, viewer_user_id="u1")
    assert "参杂" in text
    assert "其它会话" in text
    assert "来源=其它会话" in text


def test_cross_mix_peer_header_when_other_member_present() -> None:
    now = datetime(2026, 4, 19, 18, 0, 0, tzinfo=timezone.utc)
    gid = "9001"
    cur = f"group:{gid}:user:u1"
    messages = [
        Message(
            message_id="a",
            user_id="u1",
            role="user",
            raw_content="me",
            sanitized_content="me",
            created_at=now - timedelta(hours=1),
            session_id="s",
            scope_id=cur,
            scene_type="group",
            group_id=gid,
        ),
        Message(
            message_id="b",
            user_id="u2",
            role="user",
            raw_content="peer",
            sanitized_content="peer",
            created_at=now - timedelta(minutes=20),
            session_id="s",
            scope_id=f"group:{gid}:user:u2",
            scene_type="group",
            group_id=gid,
        ),
    ]
    text = build_history_reference_context(now=now, messages=messages, current_scope_id=cur, viewer_user_id="u1")
    assert "同群内其它成员" in text


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


def test_sqlite_list_other_scopes_respects_time_floor(tmp_path) -> None:
    from app.repos.sqlite_repo import SQLiteMessageRepo, SQLiteStore

    store = SQLiteStore(str(tmp_path / "mix.db"))
    repo = SQLiteMessageRepo(store)
    main = "private:main:u1"
    other = "private:side:u1"
    floor = datetime(2026, 4, 19, 12, 0, 0, tzinfo=timezone.utc)

    repo.add(
        Message(
            message_id="too_old",
            user_id="u1",
            role="user",
            raw_content="old",
            sanitized_content="old",
            created_at=floor - timedelta(hours=1),
            session_id="s",
            scope_id=other,
        )
    )
    repo.add(
        Message(
            message_id="ok",
            user_id="u1",
            role="user",
            raw_content="ok",
            sanitized_content="ok",
            created_at=floor + timedelta(minutes=5),
            session_id="s",
            scope_id=other,
        )
    )
    rows = repo.list_other_scopes_for_user_since(
        user_id="u1",
        exclude_scope_id=main,
        not_before=floor,
        limit=10,
        include_other_users=False,
        include_group_chat_messages=True,
    )
    assert len(rows) == 1
    assert rows[0].sanitized_content == "ok"


def test_sqlite_cross_mix_includes_other_group_member_when_enabled(tmp_path) -> None:
    from app.repos.sqlite_repo import SQLiteMessageRepo, SQLiteStore

    store = SQLiteStore(str(tmp_path / "mix2.db"))
    repo = SQLiteMessageRepo(store)
    gid = "g1"
    main = f"group:{gid}:user:u1"
    floor = datetime(2026, 4, 19, 12, 0, 0, tzinfo=timezone.utc)

    repo.add(
        Message(
            message_id="m_u2",
            user_id="u2",
            role="user",
            raw_content="peer",
            sanitized_content="peer",
            created_at=floor + timedelta(minutes=1),
            session_id="s",
            scope_id=f"group:{gid}:user:u2",
            scene_type="group",
            group_id=gid,
        )
    )
    rows = repo.list_other_scopes_for_user_since(
        user_id="u1",
        exclude_scope_id=main,
        not_before=floor,
        limit=10,
        include_other_users=True,
        include_group_chat_messages=True,
        viewer_scene_type="group",
        viewer_group_id=gid,
    )
    assert len(rows) == 1
    assert rows[0].user_id == "u2"


def test_sqlite_cross_mix_excludes_group_when_disabled(tmp_path) -> None:
    from app.repos.sqlite_repo import SQLiteMessageRepo, SQLiteStore

    store = SQLiteStore(str(tmp_path / "mix3.db"))
    repo = SQLiteMessageRepo(store)
    gid = "g2"
    main = f"group:{gid}:user:u1"
    floor = datetime(2026, 4, 19, 12, 0, 0, tzinfo=timezone.utc)
    repo.add(
        Message(
            message_id="gmsg",
            user_id="u2",
            role="user",
            raw_content="x",
            sanitized_content="x",
            created_at=floor + timedelta(minutes=1),
            session_id="s",
            scope_id=f"group:{gid}:user:u2",
            scene_type="group",
            group_id=gid,
        )
    )
    rows = repo.list_other_scopes_for_user_since(
        user_id="u1",
        exclude_scope_id=main,
        not_before=floor,
        limit=10,
        include_other_users=True,
        include_group_chat_messages=False,
        viewer_scene_type="group",
        viewer_group_id=gid,
    )
    assert rows == []
