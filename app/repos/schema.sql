CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    nickname TEXT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_active_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    turn_count INTEGER NOT NULL DEFAULT 0,
    last_seen_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS messages_raw (
    message_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    user_id TEXT NOT NULL REFERENCES users(user_id),
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    raw_content TEXT NOT NULL,
    emotion_score DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS messages_sanitized (
    message_id TEXT PRIMARY KEY REFERENCES messages_raw(message_id),
    sanitized_content TEXT NOT NULL,
    privacy_level SMALLINT NOT NULL DEFAULT 1,
    related_user_ids TEXT[] NOT NULL DEFAULT '{}',
    time_bucket TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_facts (
    fact_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    source_message_id TEXT NOT NULL REFERENCES messages_raw(message_id),
    fact_text TEXT NOT NULL,
    fact_type TEXT NOT NULL DEFAULT 'general',
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0.5,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS emotion_global (
    id BIGSERIAL PRIMARY KEY,
    window_start TIMESTAMPTZ NOT NULL,
    window_end TIMESTAMPTZ NOT NULL,
    avg_input_score DOUBLE PRECISION NOT NULL,
    global_emotion DOUBLE PRECISION NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS emotion_session (
    id BIGSERIAL PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    turn_index INTEGER NOT NULL,
    message_id TEXT NOT NULL REFERENCES messages_raw(message_id),
    session_emotion DOUBLE PRECISION NOT NULL,
    global_emotion DOUBLE PRECISION NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_messages_raw_user_created
ON messages_raw(user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_messages_sanitized_related_users
ON messages_sanitized USING GIN (related_user_ids);

CREATE INDEX IF NOT EXISTS idx_memory_facts_user_created
ON memory_facts(user_id, created_at DESC);
