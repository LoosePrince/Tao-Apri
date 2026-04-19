import json
from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppConfig(BaseModel):
    name: str = "Social Persona AI"
    env: str = "dev"
    debug: bool = True
    timezone: str = "Asia/Shanghai"


class StorageConfig(BaseModel):
    sqlite_db_path: str = "social_persona_ai.db"
    postgres_dsn: str = "postgresql://localhost:5432/social_persona_ai"
    vector_dsn: str = "http://localhost:6333"
    vector_collection: str = "persona_memory"


class EmotionConfig(BaseModel):
    decay: float = Field(default=0.05, ge=0.0, le=1.0)
    gain: float = Field(default=0.8, ge=0.0, le=2.0)
    max_history: int = Field(default=1000, ge=10)


class RetrievalConfig(BaseModel):
    top_k: int = Field(default=5, ge=1, le=50)
    max_rounds: int = Field(default=3, ge=1, le=10)
    min_score: float = Field(default=0.2, ge=0.0, le=1.0)
    heat_boost_weight: float = Field(default=0.15, ge=0.0, le=1.0)
    heat_decay_per_day: float = Field(default=0.08, ge=0.0, le=1.0)
    heat_increment_on_access: float = Field(default=0.12, ge=0.0, le=1.0)
    recency_window_days: int = Field(default=30, ge=1, le=365)
    cross_positive_threshold: float = Field(default=0.2, ge=0.0, le=1.0)
    cross_neutral_threshold: float = Field(default=0.35, ge=0.0, le=1.0)
    cross_negative_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    relation_access_min_strength: float = Field(default=0.2, ge=0.0, le=1.0)


class PersonaConfig(BaseModel):
    name: str = "LinXi"
    policy_notice_on_first_turn: bool = True
    assets_dir: str = "prompt_assets"


class SessionConfig(BaseModel):
    renew_after_hours: float = Field(default=3.0, ge=0.1, le=168.0)


class ProfileConfig(BaseModel):
    recent_message_limit: int = Field(default=30, ge=5, le=200)


class ConversationHistoryConfig(BaseModel):
    """Recent in-scope transcript for low-weight prompt context (older than current batch)."""

    reference_message_limit: int = Field(default=0, ge=0, le=500)


class JobsConfig(BaseModel):
    enabled: bool = False
    worker_count: int = Field(default=1, ge=1, le=4)
    queue_size: int = Field(default=1000, ge=10, le=10000)
    max_retries: int = Field(default=2, ge=0, le=10)
    dead_letter_limit: int = Field(default=200, ge=10, le=5000)
    maintenance_enabled: bool = False
    maintenance_interval_seconds: float = Field(default=60.0, ge=5.0, le=3600.0)
    emotion_window_minutes: int = Field(default=30, ge=5, le=240)


class LLMConfig(BaseModel):
    provider: str = "kilo"
    model: str = "kilo-free"
    api_key: str = ""
    base_url: str = "https://api.kilo.ai/api/gateway"
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    timeout_seconds: float = Field(default=30.0, ge=5.0, le=120.0)
    startup_healthcheck_enabled: bool = False
    retry_max_attempts: int = Field(default=3, ge=1, le=10)
    retry_backoff_seconds: float = Field(default=1.0, ge=0.0, le=30.0)
    circuit_breaker_failure_threshold: int = Field(default=3, ge=1, le=50)
    circuit_breaker_open_seconds: float = Field(default=30.0, ge=1.0, le=3600.0)


class RhythmConfig(BaseModel):
    enabled: bool = True
    silence_seconds: float = Field(default=15.0, ge=1.0, le=120.0)
    enable_max_think_seconds: bool = True
    max_think_seconds: float = Field(default=45.0, ge=5.0, le=300.0)
    cooldown_seconds: float = Field(default=2.0, ge=0.0, le=30.0)
    single_message_char_threshold: int = Field(default=200, ge=20, le=5000)
    single_message_token_threshold: int = Field(default=400, ge=20, le=8000)
    window_char_threshold: int = Field(default=600, ge=50, le=20000)
    window_token_threshold: int = Field(default=1200, ge=50, le=40000)
    enable_terminate_keywords: bool = True
    terminate_keywords: list[str] = Field(default_factory=lambda: ["算了", "不用了", "当我没说"])
    wait_timeout_seconds: float = Field(default=90.0, ge=5.0, le=600.0)


class OneBotConfig(BaseModel):
    enabled: bool = False
    ws_url: str = "http://127.0.0.1:6700"
    token: str = "[REDACTED]"
    message_format: str = "array"
    reconnect_interval_seconds: float = Field(default=3.0, ge=0.5, le=60.0)
    debug_only_user_id: int = 1377820366
    force_group_whitelist: bool = False
    group_autonomous_whitelist: list[int] = Field(default_factory=list)


class OCRConfig(BaseModel):
    enabled: bool = False
    engine: str = "rapidocr"
    max_image_mb: float = Field(default=10.0, ge=0.1, le=100.0)
    download_timeout_seconds: float = Field(default=15.0, ge=1.0, le=120.0)


class VisionConfig(BaseModel):
    enabled: bool = False
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    max_image_mb: float = Field(default=10.0, ge=0.1, le=100.0)
    download_timeout_seconds: float = Field(default=15.0, ge=1.0, le=120.0)


class ImageUnderstandingConfig(BaseModel):
    enabled: bool = False
    prefer_ocr_first: bool = True
    merge_strategy: str = "ocr_plus_vision"


class RelationPolicyConfig(BaseModel):
    """用户↔杏桃关系：多标签 + 边界阈值 + 开发者账号注入。"""

    enabled: bool = True
    allowed_tags: list[str] = Field(
        default_factory=lambda: [
            "developer",
            "friend",
            "close_friend",
            "neutral",
            "acquaintance",
            "strained",
        ]
    )
    role_priority_allowed: list[str] = Field(
        default_factory=lambda: ["neutral", "developer", "friend", "close_friend", "strained"]
    )
    default_role_priority: str = "neutral"
    default_boundary_state: str = "normal"
    developer_user_ids: list[str] = Field(default_factory=list)
    promote_developer_role_priority: bool = True
    restricted_on_negative_polarity: bool = True
    boundary_warn_trust_below: float = Field(0.35, ge=0.0, le=1.0)
    boundary_restricted_trust_below: float = Field(0.15, ge=0.0, le=1.0)
    high_intimacy_tone_hint_above: float = Field(0.72, ge=0.0, le=1.0)
    group_skip_when_restricted_without_mention: bool = False
    group_restricted_skip_trust_below: float = Field(0.12, ge=0.0, le=1.0)


class ToolRuntimeConfig(BaseModel):
    enabled: bool = False
    max_rounds: int = Field(default=4, ge=1, le=12)
    max_tool_calls_per_round: int = Field(default=3, ge=1, le=10)
    force_send_whitelist: bool = False
    allowed_send_targets: list[str] = Field(default_factory=list)
    send_rate_limit_per_minute: int = Field(default=10, ge=1, le=300)
    non_readonly_permission_behavior: str = "allow"  # allow | ask | deny
    retry_max_attempts: int = Field(default=2, ge=1, le=5)
    retry_backoff_seconds: list[float] = Field(default_factory=lambda: [0.2, 0.8, 1.6])
    retryable_error_codes: list[str] = Field(default_factory=lambda: ["timeout", "execution_failed"])
    result_budget_per_tool_chars: int = Field(default=4000, ge=256, le=20000)
    result_budget_total_chars: int = Field(default=12000, ge=1024, le=100000)
    unified_digest_max_chars: int = Field(default=8000, ge=512, le=100000)


class DelayedTaskConfig(BaseModel):
    enabled: bool = True
    poll_interval_seconds: float = Field(default=1.0, ge=0.2, le=60.0)
    claim_batch_size: int = Field(default=10, ge=1, le=200)
    stale_lease_seconds: float = Field(default=120.0, ge=5.0, le=3600.0)
    max_attempts: int = Field(default=3, ge=1, le=20)
    retry_backoff_seconds: list[float] = Field(default_factory=lambda: [10.0, 30.0, 60.0])


class Settings(BaseSettings):
    app: AppConfig = AppConfig()
    storage: StorageConfig = StorageConfig()
    emotion: EmotionConfig = EmotionConfig()
    retrieval: RetrievalConfig = RetrievalConfig()
    persona: PersonaConfig = PersonaConfig()
    session: SessionConfig = SessionConfig()
    profile: ProfileConfig = ProfileConfig()
    conversation_history: ConversationHistoryConfig = ConversationHistoryConfig()
    jobs: JobsConfig = JobsConfig()
    llm: LLMConfig = LLMConfig()
    rhythm: RhythmConfig = RhythmConfig()
    onebot: OneBotConfig = OneBotConfig()
    ocr: OCRConfig = OCRConfig()
    vision: VisionConfig = VisionConfig()
    image_understanding: ImageUnderstandingConfig = ImageUnderstandingConfig()
    tools: ToolRuntimeConfig = ToolRuntimeConfig()
    relation: RelationPolicyConfig = RelationPolicyConfig()
    delayed_task: DelayedTaskConfig = DelayedTaskConfig()

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )


settings = Settings()


class ParameterBand(BaseModel):
    label: str
    min_value: float
    max_value: float
    meaning: str
    output_guidance: str
    example_user_input: str
    example_ai_output: str


class ParameterSemanticSpec(BaseModel):
    name: str
    current_value: str
    value_range: str
    strictness_note: str
    meaning_by_band: list[ParameterBand]


def _behavior_specs_json_path() -> Path:
    return Path(__file__).resolve().parents[2] / "prompt_assets" / "param_controls" / "behavior_specs.json"


def _current_behavior_parameter_values() -> dict[str, str]:
    emotion = settings.emotion
    retrieval = settings.retrieval
    relation = settings.relation
    llm = settings.llm
    rhythm = settings.rhythm
    session = settings.session
    profile = settings.profile
    persona = settings.persona
    return {
        "EMOTION__DECAY": f"{emotion.decay:.3f}",
        "EMOTION__GAIN": f"{emotion.gain:.3f}",
        "EMOTION__MAX_HISTORY": str(emotion.max_history),
        "RETRIEVAL__TOP_K": str(retrieval.top_k),
        "RETRIEVAL__MAX_ROUNDS": str(retrieval.max_rounds),
        "RETRIEVAL__MIN_SCORE": f"{retrieval.min_score:.3f}",
        "RETRIEVAL__HEAT_BOOST_WEIGHT": f"{retrieval.heat_boost_weight:.3f}",
        "RETRIEVAL__HEAT_DECAY_PER_DAY": f"{retrieval.heat_decay_per_day:.3f}",
        "RETRIEVAL__HEAT_INCREMENT_ON_ACCESS": f"{retrieval.heat_increment_on_access:.3f}",
        "RETRIEVAL__RECENCY_WINDOW_DAYS": str(retrieval.recency_window_days),
        "RETRIEVAL__CROSS_POSITIVE_THRESHOLD": f"{retrieval.cross_positive_threshold:.3f}",
        "RETRIEVAL__CROSS_NEUTRAL_THRESHOLD": f"{retrieval.cross_neutral_threshold:.3f}",
        "RETRIEVAL__CROSS_NEGATIVE_THRESHOLD": f"{retrieval.cross_negative_threshold:.3f}",
        "RETRIEVAL__RELATION_ACCESS_MIN_STRENGTH": f"{retrieval.relation_access_min_strength:.3f}",
        "RELATION__ENABLED": str(relation.enabled).lower(),
        "RELATION__BOUNDARY_WARN_TRUST_BELOW": f"{relation.boundary_warn_trust_below:.3f}",
        "RELATION__BOUNDARY_RESTRICTED_TRUST_BELOW": f"{relation.boundary_restricted_trust_below:.3f}",
        "RELATION__DEVELOPER_USER_IDS": ",".join(str(x) for x in relation.developer_user_ids),
        "PERSONA__NAME": persona.name,
        "PERSONA__POLICY_NOTICE_ON_FIRST_TURN": str(persona.policy_notice_on_first_turn).lower(),
        "PERSONA__ASSETS_DIR": persona.assets_dir,
        "SESSION__RENEW_AFTER_HOURS": f"{session.renew_after_hours:.2f}",
        "PROFILE__RECENT_MESSAGE_LIMIT": str(profile.recent_message_limit),
        "CONVERSATION_HISTORY__REFERENCE_MESSAGE_LIMIT": str(settings.conversation_history.reference_message_limit),
        "LLM__PROVIDER": llm.provider,
        "LLM__MODEL": llm.model,
        "LLM__API_KEY": "configured" if bool(llm.api_key) else "empty",
        "LLM__BASE_URL": llm.base_url,
        "LLM__TEMPERATURE": f"{llm.temperature:.2f}",
        "LLM__TIMEOUT_SECONDS": f"{llm.timeout_seconds:.1f}",
        "LLM__STARTUP_HEALTHCHECK_ENABLED": str(llm.startup_healthcheck_enabled).lower(),
        "LLM__RETRY_MAX_ATTEMPTS": str(llm.retry_max_attempts),
        "LLM__RETRY_BACKOFF_SECONDS": f"{llm.retry_backoff_seconds:.2f}",
        "LLM__CIRCUIT_BREAKER_FAILURE_THRESHOLD": str(llm.circuit_breaker_failure_threshold),
        "LLM__CIRCUIT_BREAKER_OPEN_SECONDS": f"{llm.circuit_breaker_open_seconds:.1f}",
        "RHYTHM__ENABLED": str(rhythm.enabled).lower(),
        "RHYTHM__SILENCE_SECONDS": f"{rhythm.silence_seconds:.1f}",
        "RHYTHM__ENABLE_MAX_THINK_SECONDS": str(rhythm.enable_max_think_seconds).lower(),
        "RHYTHM__MAX_THINK_SECONDS": f"{rhythm.max_think_seconds:.1f}",
        "RHYTHM__COOLDOWN_SECONDS": f"{rhythm.cooldown_seconds:.1f}",
        "RHYTHM__SINGLE_MESSAGE_CHAR_THRESHOLD": str(rhythm.single_message_char_threshold),
        "RHYTHM__SINGLE_MESSAGE_TOKEN_THRESHOLD": str(rhythm.single_message_token_threshold),
        "RHYTHM__WINDOW_CHAR_THRESHOLD": str(rhythm.window_char_threshold),
        "RHYTHM__WINDOW_TOKEN_THRESHOLD": str(rhythm.window_token_threshold),
        "RHYTHM__ENABLE_TERMINATE_KEYWORDS": str(rhythm.enable_terminate_keywords).lower(),
        "RHYTHM__TERMINATE_KEYWORDS": ",".join(rhythm.terminate_keywords),
        "RHYTHM__WAIT_TIMEOUT_SECONDS": f"{rhythm.wait_timeout_seconds:.1f}",
    }


def build_behavior_parameter_specs() -> list[ParameterSemanticSpec]:
    with _behavior_specs_json_path().open("r", encoding="utf-8") as f:
        raw_items = json.load(f)
    current_values = _current_behavior_parameter_values()
    specs: list[ParameterSemanticSpec] = []
    for raw in raw_items:
        name = str(raw.get("name", "")).strip()
        if not name:
            continue
        payload = {
            "name": name,
            "current_value": current_values.get(name, str(raw.get("current_value", ""))),
            "value_range": str(raw.get("value_range", "")),
            "strictness_note": str(raw.get("strictness_note", "")),
            "meaning_by_band": raw.get("meaning_by_band", []),
        }
        specs.append(ParameterSemanticSpec.model_validate(payload))
    return specs
