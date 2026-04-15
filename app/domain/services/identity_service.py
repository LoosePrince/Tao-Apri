from datetime import datetime, timezone
from uuid import uuid4

from app.domain.models import Session, User
from app.repos.interfaces import SessionRepo, UserRepo


class IdentityService:
    def __init__(self, user_repo: UserRepo, session_repo: SessionRepo) -> None:
        self.user_repo = user_repo
        self.session_repo = session_repo

    def ensure_user_and_session(self, user_id: str) -> tuple[User, Session]:
        user = self.user_repo.get(user_id) or User(user_id=user_id)
        user = self.user_repo.upsert(user)

        session = self.session_repo.get_by_user_id(user_id)
        if not session:
            session = Session(session_id=str(uuid4()), user_id=user_id)
        session.last_seen_at = datetime.now(timezone.utc)
        self.session_repo.upsert(session)
        return user, session
