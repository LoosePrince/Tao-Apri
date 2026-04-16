import json
import math
import sqlite3
from datetime import datetime, timezone

from app.core.config import settings
from app.domain.models import (
    MemoryFact,
    Message,
    Session,
    User,
    UserPreference,
    UserProfile,
    UserRelation,
)
from app.repos.interfaces import (
    EmotionStateRepo,
    FactRepo,
    MessageRepo,
    PreferenceRepo,
    ProfileRepo,
    RelationRepo,
    SessionRepo,
    UserRepo,
    VectorRepo,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def _embedding(text: str, dim: int = 64) -> list[float]:
    vec = [0.0] * dim
    tokens = [token for token in text.lower().replace("，", " ").replace(",", " ").split() if token]
    if not tokens:
        return vec
    for token in tokens:
        idx = hash(token) % dim
        vec[idx] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    return sum(x * y for x, y in zip(a, b))


class SQLiteStore:
    def __init__(self, db_path: str) -> None:
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        cursor = self.conn.cursor()
        cursor.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                nickname TEXT NULL,
                first_seen_at TEXT NOT NULL,
                last_active_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                scope_id TEXT NOT NULL UNIQUE,
                user_id TEXT NOT NULL,
                scene_type TEXT NOT NULL DEFAULT 'private',
                group_id TEXT NULL,
                turn_count INTEGER NOT NULL DEFAULT 0,
                last_seen_at TEXT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                message_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                role TEXT NOT NULL,
                raw_content TEXT NOT NULL,
                sanitized_content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                session_id TEXT NOT NULL,
                scope_id TEXT NOT NULL DEFAULT '',
                scene_type TEXT NOT NULL DEFAULT 'private',
                group_id TEXT NULL,
                platform TEXT NOT NULL DEFAULT '',
                source_message_id TEXT NULL,
                emotion_score REAL NOT NULL,
                related_user_ids TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS memory_facts (
                fact_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                source_message_id TEXT NOT NULL,
                fact_text TEXT NOT NULL,
                fact_type TEXT NOT NULL,
                confidence REAL NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS vector_index (
                message_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                related_user_ids TEXT NOT NULL,
                sanitized_content TEXT NOT NULL,
                embedding TEXT NOT NULL,
                created_at TEXT NOT NULL,
                time_bucket TEXT NOT NULL,
                scope_id TEXT NOT NULL DEFAULT '',
                scene_type TEXT NOT NULL DEFAULT 'private',
                group_id TEXT NULL,
                platform TEXT NOT NULL DEFAULT '',
                heat_score REAL NOT NULL DEFAULT 0,
                access_count INTEGER NOT NULL DEFAULT 0,
                last_accessed_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS emotion_state (
                key TEXT PRIMARY KEY,
                value REAL NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS user_relations (
                source_user_id TEXT NOT NULL,
                target_user_id TEXT NOT NULL,
                polarity TEXT NOT NULL,
                strength REAL NOT NULL,
                trust_score REAL NOT NULL,
                intimacy_score REAL NOT NULL DEFAULT 0,
                dependency_score REAL NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (source_user_id, target_user_id)
            );
            CREATE TABLE IF NOT EXISTS user_preferences (
                user_id TEXT PRIMARY KEY,
                share_default TEXT NOT NULL,
                topic_visibility TEXT NOT NULL,
                explicit_deny_items TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS user_profiles (
                user_id TEXT PRIMARY KEY,
                profile_summary TEXT NOT NULL,
                preference_summary TEXT NOT NULL,
                preferred_address TEXT NOT NULL DEFAULT '',
                tone_preference TEXT NOT NULL DEFAULT '',
                schedule_state TEXT NOT NULL DEFAULT '',
                fatigue_level REAL NOT NULL DEFAULT 0,
                emotion_peak_level REAL NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );
            """
        )
        # Migrate legacy sessions table (user_id UNIQUE) to scope_id-based sessions.
        existing_tables = {
            row["name"] for row in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        if "sessions" in existing_tables:
            session_cols = {
                row["name"]
                for row in self.conn.execute("PRAGMA table_info(sessions)").fetchall()
            }
            if "scope_id" not in session_cols:
                # Legacy schema detected, rebuild sessions table.
                self.conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS sessions_new (
                        session_id TEXT PRIMARY KEY,
                        scope_id TEXT NOT NULL UNIQUE,
                        user_id TEXT NOT NULL,
                        scene_type TEXT NOT NULL DEFAULT 'private',
                        group_id TEXT NULL,
                        turn_count INTEGER NOT NULL DEFAULT 0,
                        last_seen_at TEXT NULL
                    )
                    """
                )
                self.conn.execute(
                    """
                    INSERT INTO sessions_new (session_id, scope_id, user_id, scene_type, group_id, turn_count, last_seen_at)
                    SELECT session_id, 'private:' || user_id, user_id, 'private', NULL, turn_count, last_seen_at
                    FROM sessions
                    """
                )
                self.conn.execute("DROP TABLE sessions")
                self.conn.execute("ALTER TABLE sessions_new RENAME TO sessions")

        # Migrate legacy messages/vector columns (additive).
        message_cols = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(messages)").fetchall()
        }
        if "scope_id" not in message_cols:
            self.conn.execute("ALTER TABLE messages ADD COLUMN scope_id TEXT NOT NULL DEFAULT ''")
        if "scene_type" not in message_cols:
            self.conn.execute("ALTER TABLE messages ADD COLUMN scene_type TEXT NOT NULL DEFAULT 'private'")
        if "group_id" not in message_cols:
            self.conn.execute("ALTER TABLE messages ADD COLUMN group_id TEXT NULL")
        if "platform" not in message_cols:
            self.conn.execute("ALTER TABLE messages ADD COLUMN platform TEXT NOT NULL DEFAULT ''")
        if "source_message_id" not in message_cols:
            self.conn.execute("ALTER TABLE messages ADD COLUMN source_message_id TEXT NULL")
        self.conn.execute(
            "UPDATE messages SET scope_id = 'private:' || user_id WHERE scope_id = ''"
        )

        vector_cols = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(vector_index)").fetchall()
        }
        if "scope_id" not in vector_cols:
            self.conn.execute("ALTER TABLE vector_index ADD COLUMN scope_id TEXT NOT NULL DEFAULT ''")
        if "scene_type" not in vector_cols:
            self.conn.execute("ALTER TABLE vector_index ADD COLUMN scene_type TEXT NOT NULL DEFAULT 'private'")
        if "group_id" not in vector_cols:
            self.conn.execute("ALTER TABLE vector_index ADD COLUMN group_id TEXT NULL")
        if "platform" not in vector_cols:
            self.conn.execute("ALTER TABLE vector_index ADD COLUMN platform TEXT NOT NULL DEFAULT ''")
        self.conn.execute(
            """
            UPDATE vector_index
            SET scope_id = (
                SELECT m.scope_id FROM messages m WHERE m.message_id = vector_index.message_id
            )
            WHERE scope_id = ''
            """
        )
        vector_columns = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(vector_index)").fetchall()
        }
        if "created_at" not in vector_columns:
            self.conn.execute(
                "ALTER TABLE vector_index ADD COLUMN created_at TEXT NOT NULL DEFAULT ''"
            )
        if "time_bucket" not in vector_columns:
            self.conn.execute(
                "ALTER TABLE vector_index ADD COLUMN time_bucket TEXT NOT NULL DEFAULT ''"
            )
        if "heat_score" not in vector_columns:
            self.conn.execute(
                "ALTER TABLE vector_index ADD COLUMN heat_score REAL NOT NULL DEFAULT 0"
            )
        if "access_count" not in vector_columns:
            self.conn.execute(
                "ALTER TABLE vector_index ADD COLUMN access_count INTEGER NOT NULL DEFAULT 0"
            )
        if "last_accessed_at" not in vector_columns:
            self.conn.execute(
                "ALTER TABLE vector_index ADD COLUMN last_accessed_at TEXT NOT NULL DEFAULT ''"
            )
        relation_columns = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(user_relations)").fetchall()
        }
        if "intimacy_score" not in relation_columns:
            self.conn.execute(
                "ALTER TABLE user_relations ADD COLUMN intimacy_score REAL NOT NULL DEFAULT 0"
            )
        if "dependency_score" not in relation_columns:
            self.conn.execute(
                "ALTER TABLE user_relations ADD COLUMN dependency_score REAL NOT NULL DEFAULT 0"
            )
        profile_columns = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(user_profiles)").fetchall()
        }
        if "schedule_state" not in profile_columns:
            self.conn.execute(
                "ALTER TABLE user_profiles ADD COLUMN schedule_state TEXT NOT NULL DEFAULT ''"
            )
        if "preferred_address" not in profile_columns:
            self.conn.execute(
                "ALTER TABLE user_profiles ADD COLUMN preferred_address TEXT NOT NULL DEFAULT ''"
            )
        if "tone_preference" not in profile_columns:
            self.conn.execute(
                "ALTER TABLE user_profiles ADD COLUMN tone_preference TEXT NOT NULL DEFAULT ''"
            )
        if "fatigue_level" not in profile_columns:
            self.conn.execute(
                "ALTER TABLE user_profiles ADD COLUMN fatigue_level REAL NOT NULL DEFAULT 0"
            )
        if "emotion_peak_level" not in profile_columns:
            self.conn.execute(
                "ALTER TABLE user_profiles ADD COLUMN emotion_peak_level REAL NOT NULL DEFAULT 0"
            )
        self.conn.commit()


class SQLiteUserRepo(UserRepo):
    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    def get(self, user_id: str) -> User | None:
        row = self.store.conn.execute(
            "SELECT user_id, nickname, first_seen_at, last_active_at FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if not row:
            return None
        return User(
            user_id=row["user_id"],
            nickname=row["nickname"],
            first_seen_at=_parse_dt(row["first_seen_at"]),
            last_active_at=_parse_dt(row["last_active_at"]),
        )

    def upsert(self, user: User) -> User:
        now = _now_iso()
        self.store.conn.execute(
            """
            INSERT INTO users (user_id, nickname, first_seen_at, last_active_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                nickname = COALESCE(excluded.nickname, users.nickname),
                last_active_at = excluded.last_active_at
            """,
            (user.user_id, user.nickname, now, now),
        )
        self.store.conn.commit()
        return self.get(user.user_id) or user


class SQLiteSessionRepo(SessionRepo):
    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    def get_by_scope_id(self, scope_id: str) -> Session | None:
        row = self.store.conn.execute(
            "SELECT session_id, scope_id, user_id, scene_type, group_id, turn_count, last_seen_at FROM sessions WHERE scope_id = ?",
            (scope_id,),
        ).fetchone()
        if not row:
            return None
        return Session(
            session_id=row["session_id"],
            scope_id=row["scope_id"],
            user_id=row["user_id"],
            scene_type=row["scene_type"],
            group_id=row["group_id"],
            turn_count=row["turn_count"],
            last_seen_at=_parse_dt(row["last_seen_at"]),
        )

    def upsert(self, session: Session) -> Session:
        self.store.conn.execute(
            """
            INSERT INTO sessions (session_id, scope_id, user_id, scene_type, group_id, turn_count, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(scope_id) DO UPDATE SET
                session_id = excluded.session_id,
                user_id = excluded.user_id,
                scene_type = excluded.scene_type,
                group_id = excluded.group_id,
                turn_count = excluded.turn_count,
                last_seen_at = excluded.last_seen_at
            """,
            (
                session.session_id,
                session.scope_id,
                session.user_id,
                session.scene_type,
                session.group_id,
                session.turn_count,
                session.last_seen_at.isoformat() if session.last_seen_at else None,
            ),
        )
        self.store.conn.commit()
        return session


class SQLiteMessageRepo(MessageRepo):
    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    def add(self, message: Message) -> None:
        self.store.conn.execute(
            """
            INSERT INTO messages (
                message_id, user_id, role, raw_content, sanitized_content,
                created_at, session_id, scope_id, scene_type, group_id, platform, source_message_id,
                emotion_score, related_user_ids
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message.message_id,
                message.user_id,
                message.role,
                message.raw_content,
                message.sanitized_content,
                message.created_at.isoformat(),
                message.session_id,
                message.scope_id,
                message.scene_type,
                message.group_id,
                message.platform,
                message.source_message_id,
                message.emotion_score,
                json.dumps(message.related_user_ids, ensure_ascii=False),
            ),
        )
        self.store.conn.commit()

    @staticmethod
    def _to_message(row: sqlite3.Row) -> Message:
        return Message(
            message_id=row["message_id"],
            user_id=row["user_id"],
            role=row["role"],
            raw_content=row["raw_content"],
            sanitized_content=row["sanitized_content"],
            created_at=datetime.fromisoformat(row["created_at"]),
            session_id=row["session_id"],
            scope_id=row["scope_id"],
            scene_type=row["scene_type"],
            group_id=row["group_id"],
            platform=row["platform"],
            source_message_id=row["source_message_id"],
            emotion_score=float(row["emotion_score"]),
            related_user_ids=json.loads(row["related_user_ids"]),
            retrieval_meta={},
        )

    def list_by_user(self, user_id: str, limit: int = 20) -> list[Message]:
        rows = self.store.conn.execute(
            """
            SELECT * FROM messages
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
        return [self._to_message(row) for row in reversed(rows)]

    def list_all(self, limit: int = 200) -> list[Message]:
        rows = self.store.conn.execute(
            "SELECT * FROM messages ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._to_message(row) for row in reversed(rows)]


class SQLiteFactRepo(FactRepo):
    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    def add(self, fact: MemoryFact) -> None:
        self.store.conn.execute(
            """
            INSERT INTO memory_facts (
                fact_id, user_id, source_message_id, fact_text, fact_type, confidence, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fact.fact_id,
                fact.user_id,
                fact.source_message_id,
                fact.fact_text,
                fact.fact_type,
                fact.confidence,
                fact.created_at.isoformat(),
            ),
        )
        self.store.conn.commit()

    def list_by_user(self, user_id: str, limit: int = 50) -> list[MemoryFact]:
        rows = self.store.conn.execute(
            """
            SELECT * FROM memory_facts
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
        facts: list[MemoryFact] = []
        for row in rows:
            facts.append(
                MemoryFact(
                    fact_id=row["fact_id"],
                    user_id=row["user_id"],
                    source_message_id=row["source_message_id"],
                    fact_text=row["fact_text"],
                    fact_type=row["fact_type"],
                    confidence=float(row["confidence"]),
                    created_at=datetime.fromisoformat(row["created_at"]),
                )
            )
        return facts


class SQLiteVectorRepo(VectorRepo):
    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    def add_memory(self, message: Message) -> None:
        vec = _embedding(message.sanitized_content)
        time_bucket = message.created_at.strftime("%Y-%m-%dT%H")
        self.store.conn.execute(
            """
            INSERT INTO vector_index (
                message_id, user_id, related_user_ids, sanitized_content, embedding, created_at, time_bucket,
                scope_id, scene_type, group_id, platform,
                heat_score, access_count, last_accessed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(message_id) DO UPDATE SET
                user_id = excluded.user_id,
                related_user_ids = excluded.related_user_ids,
                sanitized_content = excluded.sanitized_content,
                embedding = excluded.embedding,
                created_at = excluded.created_at,
                time_bucket = excluded.time_bucket,
                scope_id = excluded.scope_id,
                scene_type = excluded.scene_type,
                group_id = excluded.group_id,
                platform = excluded.platform
            """,
            (
                message.message_id,
                message.user_id,
                json.dumps(message.related_user_ids, ensure_ascii=False),
                message.sanitized_content,
                json.dumps(vec),
                message.created_at.isoformat(),
                time_bucket,
                message.scope_id,
                message.scene_type,
                message.group_id,
                message.platform,
                0.0,
                0,
                "",
            ),
        )
        self.store.conn.commit()

    def search(
        self,
        query: str,
        user_id: str,
        limit: int = 5,
        min_score: float = 0.2,
        recency_window_days: int = 30,
    ) -> list[Message]:
        query_vec = _embedding(query)
        cutoff = datetime.now(timezone.utc).timestamp() - (recency_window_days * 24 * 60 * 60)
        now = datetime.now(timezone.utc)
        rows = self.store.conn.execute(
            """
            SELECT
                m.message_id, m.user_id, m.role, m.raw_content, m.sanitized_content,
                m.created_at, m.session_id, m.scope_id, m.scene_type, m.group_id, m.platform, m.source_message_id, m.emotion_score, m.related_user_ids,
                v.embedding, v.created_at as v_created_at, v.heat_score, v.access_count, v.last_accessed_at
            FROM vector_index v
            JOIN messages m ON m.message_id = v.message_id
            """
        ).fetchall()
        scored: list[tuple[float, Message, float]] = []
        for row in rows:
            created_at_text = row["v_created_at"] or row["created_at"]
            if datetime.fromisoformat(created_at_text).timestamp() < cutoff:
                continue
            related_user_ids = json.loads(row["related_user_ids"])
            base = _cosine(query_vec, json.loads(row["embedding"]))
            is_related = row["user_id"] == user_id or user_id in related_user_ids
            last_accessed_at_text = row["last_accessed_at"] or created_at_text
            last_accessed_at = datetime.fromisoformat(last_accessed_at_text)
            days_since_access = max(0.0, (now - last_accessed_at).total_seconds() / (24 * 60 * 60))
            stored_heat = float(row["heat_score"])
            decayed_heat = max(0.0, stored_heat - settings.retrieval.heat_decay_per_day * days_since_access)
            relation_boost = 0.2 if is_related else 0.0
            heat_boost = settings.retrieval.heat_boost_weight * decayed_heat
            score = base + relation_boost + heat_boost
            if score < min_score:
                continue
            scored.append(
                (
                    score,
                    Message(
                        message_id=row["message_id"],
                        user_id=row["user_id"],
                        role=row["role"],
                        raw_content=row["raw_content"],
                        sanitized_content=row["sanitized_content"],
                        created_at=datetime.fromisoformat(row["created_at"]),
                        session_id=row["session_id"],
                        scope_id=row["scope_id"],
                        scene_type=row["scene_type"],
                        group_id=row["group_id"],
                        platform=row["platform"],
                        source_message_id=row["source_message_id"],
                        emotion_score=float(row["emotion_score"]),
                        related_user_ids=related_user_ids,
                        retrieval_meta={
                            "base_score": round(base, 6),
                            "relation_boost": round(relation_boost, 6),
                            "heat_boost": round(heat_boost, 6),
                            "decayed_heat": round(decayed_heat, 6),
                            "days_since_access": round(days_since_access, 6),
                            "access_count": int(row["access_count"]),
                            "final_score": round(score, 6),
                        },
                    ),
                    decayed_heat,
                )
            )
        scored.sort(key=lambda item: item[0], reverse=True)
        selected = scored[:limit]
        now_iso = now.isoformat()
        for _, message, decayed_heat in selected:
            new_heat = min(1.0, decayed_heat + settings.retrieval.heat_increment_on_access)
            self.store.conn.execute(
                """
                UPDATE vector_index
                SET heat_score = ?, access_count = access_count + 1, last_accessed_at = ?
                WHERE message_id = ?
                """,
                (new_heat, now_iso, message.message_id),
            )
        if selected:
            self.store.conn.commit()
        return [item[1] for item in selected]

    def run_maintenance(self) -> dict[str, int]:
        now = datetime.now(timezone.utc)
        updated = 0
        removed = 0
        rows = self.store.conn.execute(
            "SELECT message_id, heat_score, last_accessed_at, created_at FROM vector_index"
        ).fetchall()
        for row in rows:
            created_at_text = row["created_at"]
            last_accessed_at_text = row["last_accessed_at"] or created_at_text
            last_accessed_at = datetime.fromisoformat(last_accessed_at_text)
            days_since_access = max(0.0, (now - last_accessed_at).total_seconds() / (24 * 60 * 60))
            heat_score = float(row["heat_score"])
            decayed = max(0.0, heat_score - settings.retrieval.heat_decay_per_day * days_since_access)
            if abs(decayed - heat_score) > 1e-9:
                self.store.conn.execute(
                    "UPDATE vector_index SET heat_score = ?, last_accessed_at = ? WHERE message_id = ?",
                    (decayed, now.isoformat(), row["message_id"]),
                )
                updated += 1
            if decayed <= 0.0 and days_since_access > settings.retrieval.recency_window_days * 1.5:
                self.store.conn.execute(
                    "DELETE FROM vector_index WHERE message_id = ?",
                    (row["message_id"],),
                )
                removed += 1
        if updated or removed:
            self.store.conn.commit()
        return {"updated": updated, "removed": removed}


class SQLiteEmotionStateRepo(EmotionStateRepo):
    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    def get_global_emotion(self) -> float:
        row = self.store.conn.execute(
            "SELECT value FROM emotion_state WHERE key = 'global_emotion'"
        ).fetchone()
        if not row:
            return 0.0
        return float(row["value"])

    def set_global_emotion(self, value: float) -> None:
        self.store.conn.execute(
            """
            INSERT INTO emotion_state (key, value, updated_at)
            VALUES ('global_emotion', ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (value, _now_iso()),
        )
        self.store.conn.commit()


class SQLiteRelationRepo(RelationRepo):
    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    def get(self, source_user_id: str, target_user_id: str) -> UserRelation | None:
        row = self.store.conn.execute(
            """
            SELECT source_user_id, target_user_id, polarity, strength, trust_score, intimacy_score, dependency_score, updated_at
            FROM user_relations
            WHERE source_user_id = ? AND target_user_id = ?
            """,
            (source_user_id, target_user_id),
        ).fetchone()
        if not row:
            return None
        return UserRelation(
            source_user_id=row["source_user_id"],
            target_user_id=row["target_user_id"],
            polarity=row["polarity"],
            strength=float(row["strength"]),
            trust_score=float(row["trust_score"]),
            intimacy_score=float(row["intimacy_score"]),
            dependency_score=float(row["dependency_score"]),
            updated_at=_parse_dt(row["updated_at"]),
        )

    def upsert(self, relation: UserRelation) -> UserRelation:
        now = _now_iso()
        self.store.conn.execute(
            """
            INSERT INTO user_relations (
                source_user_id, target_user_id, polarity, strength, trust_score, intimacy_score, dependency_score, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_user_id, target_user_id) DO UPDATE SET
                polarity = excluded.polarity,
                strength = excluded.strength,
                trust_score = excluded.trust_score,
                intimacy_score = excluded.intimacy_score,
                dependency_score = excluded.dependency_score,
                updated_at = excluded.updated_at
            """,
            (
                relation.source_user_id,
                relation.target_user_id,
                relation.polarity,
                relation.strength,
                relation.trust_score,
                relation.intimacy_score,
                relation.dependency_score,
                now,
            ),
        )
        self.store.conn.commit()
        return self.get(relation.source_user_id, relation.target_user_id) or relation


class SQLitePreferenceRepo(PreferenceRepo):
    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    def get(self, user_id: str) -> UserPreference | None:
        row = self.store.conn.execute(
            """
            SELECT user_id, share_default, topic_visibility, explicit_deny_items, updated_at
            FROM user_preferences
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
        if not row:
            return None
        return UserPreference(
            user_id=row["user_id"],
            share_default=row["share_default"],
            topic_visibility=json.loads(row["topic_visibility"] or "{}"),
            explicit_deny_items=json.loads(row["explicit_deny_items"] or "[]"),
            updated_at=_parse_dt(row["updated_at"]),
        )

    def upsert(self, preference: UserPreference) -> UserPreference:
        now = _now_iso()
        self.store.conn.execute(
            """
            INSERT INTO user_preferences (
                user_id, share_default, topic_visibility, explicit_deny_items, updated_at
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                share_default = excluded.share_default,
                topic_visibility = excluded.topic_visibility,
                explicit_deny_items = excluded.explicit_deny_items,
                updated_at = excluded.updated_at
            """,
            (
                preference.user_id,
                preference.share_default,
                json.dumps(preference.topic_visibility, ensure_ascii=False),
                json.dumps(preference.explicit_deny_items, ensure_ascii=False),
                now,
            ),
        )
        self.store.conn.commit()
        return self.get(preference.user_id) or preference


class SQLiteProfileRepo(ProfileRepo):
    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    def get(self, user_id: str) -> UserProfile | None:
        row = self.store.conn.execute(
            """
            SELECT user_id, profile_summary, preference_summary, preferred_address, tone_preference, schedule_state, fatigue_level, emotion_peak_level, updated_at
            FROM user_profiles
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
        if not row:
            return None
        return UserProfile(
            user_id=row["user_id"],
            profile_summary=row["profile_summary"],
            preference_summary=row["preference_summary"],
            preferred_address=row["preferred_address"],
            tone_preference=row["tone_preference"],
            schedule_state=row["schedule_state"],
            fatigue_level=float(row["fatigue_level"]),
            emotion_peak_level=float(row["emotion_peak_level"]),
            updated_at=_parse_dt(row["updated_at"]),
        )

    def upsert(self, profile: UserProfile) -> UserProfile:
        now = _now_iso()
        self.store.conn.execute(
            """
            INSERT INTO user_profiles (
                user_id, profile_summary, preference_summary, preferred_address, tone_preference, schedule_state, fatigue_level, emotion_peak_level, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                profile_summary = excluded.profile_summary,
                preference_summary = excluded.preference_summary,
                preferred_address = excluded.preferred_address,
                tone_preference = excluded.tone_preference,
                schedule_state = excluded.schedule_state,
                fatigue_level = excluded.fatigue_level,
                emotion_peak_level = excluded.emotion_peak_level,
                updated_at = excluded.updated_at
            """,
            (
                profile.user_id,
                profile.profile_summary,
                profile.preference_summary,
                profile.preferred_address,
                profile.tone_preference,
                profile.schedule_state,
                profile.fatigue_level,
                profile.emotion_peak_level,
                now,
            ),
        )
        self.store.conn.commit()
        return self.get(profile.user_id) or profile
