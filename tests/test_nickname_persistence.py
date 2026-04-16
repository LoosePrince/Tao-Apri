from __future__ import annotations

from app.domain.services.identity_service import IdentityService
from app.repos.sqlite_repo import SQLiteSessionRepo, SQLiteStore, SQLiteUserRepo


def test_identity_service_updates_user_nickname(tmp_path) -> None:
    store = SQLiteStore(str(tmp_path / "nick.db"))
    user_repo = SQLiteUserRepo(store)
    session_repo = SQLiteSessionRepo(store)
    service = IdentityService(user_repo=user_repo, session_repo=session_repo)

    service.ensure_user_and_session("u1", nickname="QQ昵称A")
    user = user_repo.get("u1")
    assert user is not None
    assert user.nickname == "QQ昵称A"

    # 不传 nickname / 传空字符串，不应覆盖已存在昵称
    service.ensure_user_and_session("u1")
    user2 = user_repo.get("u1")
    assert user2 is not None
    assert user2.nickname == "QQ昵称A"

    service.ensure_user_and_session("u1", nickname="   ")
    user3 = user_repo.get("u1")
    assert user3 is not None
    assert user3.nickname == "QQ昵称A"

    # 传入新 nickname 应更新
    service.ensure_user_and_session("u1", nickname="QQ昵称B")
    user4 = user_repo.get("u1")
    assert user4 is not None
    assert user4.nickname == "QQ昵称B"

