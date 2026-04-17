from __future__ import annotations

from datetime import datetime, timezone

from app.core.config import settings
from app.core.container import Container
from app.domain.models import Message
from app.repos.sqlite_repo import SQLiteStore, SQLiteVectorRepo


def test_postgres_dsn_can_influence_resolved_sqlite_path() -> None:
    old_sqlite_path = settings.storage.sqlite_db_path
    old_postgres_dsn = settings.storage.postgres_dsn
    settings.storage.sqlite_db_path = "social_persona_ai.db"
    settings.storage.postgres_dsn = "postgresql://user:pass@localhost:5432/taoapri_runtime"
    try:
        assert Container._resolve_sqlite_db_path() == "taoapri_runtime.db"
    finally:
        settings.storage.sqlite_db_path = old_sqlite_path
        settings.storage.postgres_dsn = old_postgres_dsn


def test_vector_collection_filters_retrieval_results(tmp_path) -> None:  # type: ignore[no-untyped-def]
    old_collection = settings.storage.vector_collection
    try:
        store = SQLiteStore(str(tmp_path / "vector-collection.db"))
        repo = SQLiteVectorRepo(store)
        created_at = datetime.now(timezone.utc)
        message = Message(
            message_id="m1",
            user_id="u1",
            role="user",
            raw_content="hello world",
            sanitized_content="hello world",
            created_at=created_at,
            session_id="s1",
            scope_id="private:u1",
            scene_type="private",
            platform="onebot",
        )
        settings.storage.vector_collection = "collection_a"
        repo.add_memory(message)

        settings.storage.vector_collection = "collection_b"
        assert repo.search("hello", "u1", limit=5, min_score=0.0) == []
    finally:
        settings.storage.vector_collection = old_collection
