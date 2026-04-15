from abc import ABC, abstractmethod

from app.domain.models import MemoryFact, Message, Session, User, UserPreference, UserProfile, UserRelation


class UserRepo(ABC):
    @abstractmethod
    def get(self, user_id: str) -> User | None: ...

    @abstractmethod
    def upsert(self, user: User) -> User: ...


class SessionRepo(ABC):
    @abstractmethod
    def get_by_user_id(self, user_id: str) -> Session | None: ...

    @abstractmethod
    def upsert(self, session: Session) -> Session: ...


class MessageRepo(ABC):
    @abstractmethod
    def add(self, message: Message) -> None: ...

    @abstractmethod
    def list_by_user(self, user_id: str, limit: int = 20) -> list[Message]: ...

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
