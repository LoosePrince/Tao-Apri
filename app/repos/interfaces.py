from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from app.domain.models import DelayedTask, MemoryFact, Message, Session, User, UserPreference, UserProfile, UserRelation


class UserRepo(ABC):
    @abstractmethod
    def get(self, user_id: str) -> User | None: ...

    @abstractmethod
    def upsert(self, user: User) -> User: ...


class SessionRepo(ABC):
    @abstractmethod
    def get_by_scope_id(self, scope_id: str) -> Session | None: ...

    @abstractmethod
    def upsert(self, session: Session) -> Session: ...


class MessageRepo(ABC):
    @abstractmethod
    def add(self, message: Message) -> None: ...

    @abstractmethod
    def list_by_user(self, user_id: str, limit: int = 20) -> list[Message]: ...

    @abstractmethod
    def list_by_scope(self, scope_id: str, limit: int = 50) -> list[Message]:
        """Most recent `limit` messages in the conversation scope, oldest-first."""

    @abstractmethod
    def list_other_scopes_for_user_since(
        self,
        *,
        user_id: str,
        exclude_scope_id: str,
        not_before: datetime,
        limit: int,
        include_other_users: bool = False,
        include_group_chat_messages: bool = True,
        viewer_scene_type: str = "private",
        viewer_group_id: str | None = None,
    ) -> list[Message]:
        """
        Cross-mix pool: oldest-first, created_at >= not_before, scope_id != exclude_scope_id.

        Default: same `user_id` only. If `include_other_users` and viewer is group with `viewer_group_id`,
        also includes other members' group messages in that group. If `include_group_chat_messages` is
        false, rows with scene_type == \"group\" are excluded.
        """

    @abstractmethod
    def list_all(self, limit: int = 200) -> list[Message]: ...


class FactRepo(ABC):
    @abstractmethod
    def add(self, fact: MemoryFact) -> None: ...

    @abstractmethod
    def list_by_user(self, user_id: str, limit: int = 50) -> list[MemoryFact]: ...


class VectorRepo(ABC):
    @abstractmethod
    def add_memory(self, message: Message) -> None: ...

    @abstractmethod
    def search(
        self,
        query: str,
        user_id: str,
        limit: int = 5,
        min_score: float = 0.2,
        recency_window_days: int = 30,
    ) -> list[Message]: ...

    @abstractmethod
    def run_maintenance(self) -> dict[str, int]: ...


class EmotionStateRepo(ABC):
    @abstractmethod
    def get_global_emotion(self) -> float: ...

    @abstractmethod
    def set_global_emotion(self, value: float) -> None: ...


class RelationRepo(ABC):
    @abstractmethod
    def get(self, source_user_id: str, target_user_id: str) -> UserRelation | None: ...

    @abstractmethod
    def upsert(self, relation: UserRelation) -> UserRelation: ...


class PreferenceRepo(ABC):
    @abstractmethod
    def get(self, user_id: str) -> UserPreference | None: ...

    @abstractmethod
    def upsert(self, preference: UserPreference) -> UserPreference: ...


class ProfileRepo(ABC):
    @abstractmethod
    def get(self, user_id: str) -> UserProfile | None: ...

    @abstractmethod
    def upsert(self, profile: UserProfile) -> UserProfile: ...


class DelayedTaskRepo(ABC):
    @abstractmethod
    def enqueue(self, task: DelayedTask) -> DelayedTask: ...

    @abstractmethod
    def claim_due(self, *, now_iso: str, limit: int, worker_id: str) -> list[DelayedTask]: ...

    @abstractmethod
    def mark_done(self, task_id: str) -> None: ...

    @abstractmethod
    def mark_retry(self, *, task_id: str, next_run_at_iso: str, last_error: str) -> None: ...

    @abstractmethod
    def mark_dead(self, *, task_id: str, last_error: str) -> None: ...

    @abstractmethod
    def cancel(self, task_id: str) -> None: ...

    @abstractmethod
    def requeue_stale_running(self, *, stale_before_iso: str) -> int: ...

    @abstractmethod
    def get(self, task_id: str) -> DelayedTask | None: ...

    @abstractmethod
    def list_tasks(
        self,
        *,
        scope_id: str | None = None,
        status: str | None = None,
        limit: int = 20,
    ) -> list[DelayedTask]: ...
