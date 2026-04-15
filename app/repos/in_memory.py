from collections import defaultdict
from datetime import datetime, timezone
from uuid import uuid4

from app.domain.models import MemoryFact, Message, Session, User
from app.repos.interfaces import FactRepo, MessageRepo, SessionRepo, UserRepo, VectorRepo


def _tokenize(text: str) -> set[str]:
    return {part for part in text.lower().replace("，", " ").replace(",", " ").split() if part}


def _jaccard_score(a: str, b: str) -> float:
    sa = _tokenize(a)
    sb = _tokenize(b)
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


class InMemoryUserRepo(UserRepo):
    def __init__(self) -> None:
        self._users: dict[str, User] = {}

    def get(self, user_id: str) -> User | None:
        return self._users.get(user_id)

    def upsert(self, user: User) -> User:
        now = datetime.now(timezone.utc)
        existing = self._users.get(user.user_id)
        if existing:
            existing.nickname = user.nickname or existing.nickname
            existing.last_active_at = now
            return existing
        user.first_seen_at = now
        user.last_active_at = now
        self._users[user.user_id] = user
        return user


class InMemorySessionRepo(SessionRepo):
    def __init__(self) -> None:
        self._sessions_by_user: dict[str, Session] = {}

    def get_by_user_id(self, user_id: str) -> Session | None:
        return self._sessions_by_user.get(user_id)

    def upsert(self, session: Session) -> Session:
        self._sessions_by_user[session.user_id] = session
        return session

    @staticmethod
    def new_session(user_id: str) -> Session:
        return Session(session_id=str(uuid4()), user_id=user_id)


class InMemoryMessageRepo(MessageRepo):
    def __init__(self) -> None:
        self._messages: list[Message] = []
        self._by_user: defaultdict[str, list[Message]] = defaultdict(list)

    def add(self, message: Message) -> None:
        self._messages.append(message)
        self._by_user[message.user_id].append(message)

    def list_by_user(self, user_id: str, limit: int = 20) -> list[Message]:
        return self._by_user[user_id][-limit:]

    def list_all(self, limit: int = 200) -> list[Message]:
        return self._messages[-limit:]


class InMemoryVectorRepo(VectorRepo):
    def __init__(self) -> None:
        self._memories: list[Message] = []
        self._heat: dict[str, float] = {}

    def add_memory(self, message: Message) -> None:
        self._memories.append(message)
        self._heat[message.message_id] = 0.0

    def search(
        self,
        query: str,
        user_id: str,
        limit: int = 5,
        min_score: float = 0.2,
        recency_window_days: int = 30,
    ) -> list[Message]:
        scored: list[tuple[float, Message]] = []
        for memory in self._memories:
            # Prefer current user + related memories, but keep non-isolated option.
            is_related = memory.user_id == user_id or user_id in memory.related_user_ids
            base_score = _jaccard_score(query, memory.sanitized_content)
            heat = self._heat.get(memory.message_id, 0.0)
            relation_boost = 0.2 if is_related else 0.0
            heat_boost = 0.15 * heat
            boosted = base_score + relation_boost + heat_boost
            if boosted >= min_score:
                memory.retrieval_meta = {
                    "base_score": round(base_score, 6),
                    "relation_boost": round(relation_boost, 6),
                    "heat_boost": round(heat_boost, 6),
                    "decayed_heat": round(heat, 6),
                    "days_since_access": 0.0,
                    "access_count": 0,
                    "final_score": round(boosted, 6),
                }
                scored.append((boosted, memory))
        scored.sort(key=lambda item: item[0], reverse=True)
        selected = [item[1] for item in scored[:limit]]
        for memory in selected:
            current = self._heat.get(memory.message_id, 0.0)
            self._heat[memory.message_id] = min(1.0, current + 0.12)
        return selected


class InMemoryFactRepo(FactRepo):
    def __init__(self) -> None:
        self._facts: list[MemoryFact] = []
        self._by_user: defaultdict[str, list[MemoryFact]] = defaultdict(list)

    def add(self, fact: MemoryFact) -> None:
        self._facts.append(fact)
        self._by_user[fact.user_id].append(fact)

    def list_by_user(self, user_id: str, limit: int = 50) -> list[MemoryFact]:
        return self._by_user[user_id][-limit:]
