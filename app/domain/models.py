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
    scope_id: str
    user_id: str
    scene_type: str = "private"  # private | group
    group_id: str | None = None
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
    scope_id: str = ""
    scene_type: str = "private"  # private | group
    group_id: str | None = None
    platform: str = ""
    source_message_id: str | None = None
    emotion_score: float = 0.0
    related_user_ids: list[str] = field(default_factory=list)
    retrieval_meta: dict[str, float | int | str] = field(default_factory=dict)


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
    preferred_address: str = ""
    tone_preference: str = ""
    schedule_state: str = ""
    fatigue_level: float = 0.0
    emotion_peak_level: float = 0.0
    updated_at: datetime | None = None


@dataclass(slots=True)
class DelayedTask:
    task_id: str
    run_at: datetime
    status: str = "pending"  # pending | running | done | failed | cancelled | dead
    description: str = ""
    reason: str = ""
    trigger_source: str = ""
    payload_json: str = "{}"
    scope_id: str = ""
    attempt_count: int = 0
    max_attempts: int = 3
    last_error: str = ""
    claimed_by: str = ""
    claimed_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
