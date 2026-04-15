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


@dataclass(slots=True)
class UserRelation:
    source_user_id: str
    target_user_id: str
    polarity: str = "neutral"  # positive | neutral | negative
    strength: float = 0.0
    trust_score: float = 0.0
    intimacy_score: float = 0.0
    dependency_score: float = 0.0
    updated_at: datetime | None = None


@dataclass(slots=True)
class UserPreference:
    user_id: str
    share_default: str = "deny"  # allow | deny
    topic_visibility: dict[str, str] = field(default_factory=dict)  # topic -> allow | deny
    explicit_deny_items: list[str] = field(default_factory=list)
    updated_at: datetime | None = None


@dataclass(slots=True)
class UserProfile:
    user_id: str
    profile_summary: str = ""
    preference_summary: str = ""
    updated_at: datetime | None = None
