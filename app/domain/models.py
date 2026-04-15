from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class User:
    user_id: str
    nickname: str | None = None
    first_seen_at: datetime | None = None
    last_active_at: datetime | None = None


@dataclass(slots=True)
class Session:
    session_id: str
    user_id: str
    turn_count: int = 0
    last_seen_at: datetime | None = None


@dataclass(slots=True)
class Message:
    message_id: str
    user_id: str
    role: str
    raw_content: str
    sanitized_content: str
    created_at: datetime
    session_id: str
    emotion_score: float = 0.0
    related_user_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RetrievedMemory:
    content: str
    score: float
    source: str


@dataclass(slots=True)
class MemoryFact:
    fact_id: str
    user_id: str
    source_message_id: str
    fact_text: str
    fact_type: str
    confidence: float
    created_at: datetime
