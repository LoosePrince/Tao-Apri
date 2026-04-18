from collections import defaultdict
from datetime import datetime, timezone
from uuid import uuid4

from app.core.config import settings
from app.domain.models import DelayedTask, MemoryFact, Message, Session, User
from app.repos.interfaces import DelayedTaskRepo, FactRepo, MessageRepo, SessionRepo, UserRepo, VectorRepo


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
        self._sessions_by_scope: dict[str, Session] = {}

    def get_by_scope_id(self, scope_id: str) -> Session | None:
        return self._sessions_by_scope.get(scope_id)

    def upsert(self, session: Session) -> Session:
        self._sessions_by_scope[session.scope_id] = session
        return session

    @staticmethod
    def new_session(scope_id: str, user_id: str) -> Session:
        return Session(session_id=str(uuid4()), scope_id=scope_id, user_id=user_id)


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

    def get_latest_text_by_source_message_id(self, source_message_id: str) -> str:
        key = (source_message_id or "").strip()
        if not key:
            return ""
        for message in reversed(self._messages):
            if (message.source_message_id or "").strip() == key:
                return message.raw_content.strip()
        return ""


class InMemoryVectorRepo(VectorRepo):
    def __init__(self) -> None:
        self._memories: list[Message] = []
        self._heat: dict[str, float] = {}
        self._collection_by_message_id: dict[str, str] = {}

    def add_memory(self, message: Message) -> None:
        self._memories.append(message)
        self._heat[message.message_id] = 0.0
        self._collection_by_message_id[message.message_id] = settings.storage.vector_collection

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
            if self._collection_by_message_id.get(memory.message_id, "") != settings.storage.vector_collection:
                continue
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

    def run_maintenance(self) -> dict[str, int]:
        updated = 0
        removed = 0
        for message_id, value in list(self._heat.items()):
            new_value = max(0.0, value - 0.02)
            if abs(new_value - value) > 1e-9:
                self._heat[message_id] = new_value
                updated += 1
        return {"updated": updated, "removed": removed}

    def run_maintenance(self) -> dict[str, int]:
        updated = 0
        removed = 0
        for message_id, value in list(self._heat.items()):
            new_value = max(0.0, value - 0.02)
            if abs(new_value - value) > 1e-9:
                self._heat[message_id] = new_value
                updated += 1
        return {"updated": updated, "removed": removed}


class InMemoryFactRepo(FactRepo):
    def __init__(self) -> None:
        self._facts: list[MemoryFact] = []
        self._by_user: defaultdict[str, list[MemoryFact]] = defaultdict(list)

    def add(self, fact: MemoryFact) -> None:
        self._facts.append(fact)
        self._by_user[fact.user_id].append(fact)

    def list_by_user(self, user_id: str, limit: int = 50) -> list[MemoryFact]:
        return self._by_user[user_id][-limit:]


class InMemoryDelayedTaskRepo(DelayedTaskRepo):
    def __init__(self) -> None:
        self._tasks: dict[str, DelayedTask] = {}

    def enqueue(self, task: DelayedTask) -> DelayedTask:
        now = datetime.now(timezone.utc)
        if task.created_at is None:
            task.created_at = now
        task.updated_at = now
        self._tasks[task.task_id] = task
        return task

    def claim_due(self, *, now_iso: str, limit: int, worker_id: str) -> list[DelayedTask]:
        now = datetime.fromisoformat(now_iso)
        due = [
            task
            for task in self._tasks.values()
            if task.status == "pending" and task.run_at <= now
        ]
        due.sort(key=lambda item: item.run_at)
        claimed = due[: max(1, limit)]
        for task in claimed:
            task.status = "running"
            task.claimed_by = worker_id
            task.claimed_at = now
            task.updated_at = now
        return claimed

    def mark_done(self, task_id: str) -> None:
        task = self._tasks.get(task_id)
        if not task:
            return
        task.status = "done"
        task.updated_at = datetime.now(timezone.utc)

    def mark_retry(self, *, task_id: str, next_run_at_iso: str, last_error: str) -> None:
        task = self._tasks.get(task_id)
        if not task:
            return
        task.status = "pending"
        task.run_at = datetime.fromisoformat(next_run_at_iso)
        task.attempt_count += 1
        task.last_error = last_error
        task.claimed_by = ""
        task.claimed_at = None
        task.updated_at = datetime.now(timezone.utc)

    def mark_dead(self, *, task_id: str, last_error: str) -> None:
        task = self._tasks.get(task_id)
        if not task:
            return
        task.status = "dead"
        task.last_error = last_error
        task.updated_at = datetime.now(timezone.utc)

    def cancel(self, task_id: str) -> None:
        task = self._tasks.get(task_id)
        if not task:
            return
        task.status = "cancelled"
        task.updated_at = datetime.now(timezone.utc)

    def requeue_stale_running(self, *, stale_before_iso: str) -> int:
        stale_before = datetime.fromisoformat(stale_before_iso)
        count = 0
        now = datetime.now(timezone.utc)
        for task in self._tasks.values():
            if task.status == "running" and task.claimed_at and task.claimed_at <= stale_before:
                task.status = "pending"
                task.claimed_by = ""
                task.claimed_at = None
                task.updated_at = now
                count += 1
        return count

    def get(self, task_id: str) -> DelayedTask | None:
        return self._tasks.get(task_id)

    def list_tasks(
        self,
        *,
        scope_id: str | None = None,
        status: str | None = None,
        limit: int = 20,
    ) -> list[DelayedTask]:
        tasks = list(self._tasks.values())
        if scope_id and scope_id.strip():
            scoped = scope_id.strip()
            tasks = [task for task in tasks if task.scope_id == scoped]
        if status and status.strip():
            wanted = status.strip()
            tasks = [task for task in tasks if task.status == wanted]
        tasks.sort(key=lambda item: item.run_at, reverse=True)
        return tasks[: max(1, min(200, limit))]
