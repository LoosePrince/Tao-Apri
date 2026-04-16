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
    group_autonomous_whitelist: list[int] = Field(default_factory=list)


class Settings(BaseSettings):
    app: AppConfig = AppConfig()
    storage: StorageConfig = StorageConfig()
    emotion: EmotionConfig = EmotionConfig()
    retrieval: RetrievalConfig = RetrievalConfig()
    persona: PersonaConfig = PersonaConfig()
    session: SessionConfig = SessionConfig()
    profile: ProfileConfig = ProfileConfig()
    jobs: JobsConfig = JobsConfig()
    llm: LLMConfig = LLMConfig()
    rhythm: RhythmConfig = RhythmConfig()
    onebot: OneBotConfig = OneBotConfig()

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )


settings = Settings()
