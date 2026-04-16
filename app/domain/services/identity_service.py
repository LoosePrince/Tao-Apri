from datetime import datetime, timezone
from uuid import uuid4

from app.core.config import settings
from app.domain.conversation_scope import ConversationScope
from app.domain.models import Session, User
from app.repos.interfaces import SessionRepo, UserRepo


class IdentityService:
    def __init__(self, user_repo: UserRepo, session_repo: SessionRepo) -> None:
        self.user_repo = user_repo
        self.session_repo = session_repo

    def ensure_user_and_session(
        self, scope: ConversationScope, *, nickname: str | None = None
    ) -> tuple[User, Session]:
        user_id = scope.actor_user_id
        user = self.user_repo.get(user_id) or User(user_id=user_id)
        if nickname:
            nick = nickname.strip()
            # 仅在上游提供非空 nickname 时更新，避免无信息覆盖已有昵称。
            if nick and (not user.nickname or user.nickname != nick):
                user.nickname = nick
        user = self.user_repo.upsert(user)

        session = self.session_repo.get_by_scope_id(scope.scope_id)
        now = datetime.now(timezone.utc)
        renew_delta_seconds = settings.session.renew_after_hours * 60 * 60
        should_renew = False
        if session and session.last_seen_at:
            inactive_seconds = (now - session.last_seen_at).total_seconds()
            should_renew = inactive_seconds >= renew_delta_seconds

        if not session or should_renew:
            session = Session(
                session_id=str(uuid4()),
                scope_id=scope.scope_id,
                user_id=user_id,
                scene_type=scope.scene_type,
                group_id=scope.group_id,
            )
        session.last_seen_at = now
        self.session_repo.upsert(session)
        return user, session
