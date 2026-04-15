from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.domain.services.memory_writer import MemoryWriter
from app.repos.sqlite_repo import SQLiteFactRepo, SQLiteMessageRepo, SQLiteStore, SQLiteVectorRepo


def test_memory_heat_updates_on_retrieval(tmp_path) -> None:
    store = SQLiteStore(str(tmp_path / "heat.db"))
    message_repo = SQLiteMessageRepo(store)
    vector_repo = SQLiteVectorRepo(store)
    fact_repo = SQLiteFactRepo(store)
    writer = MemoryWriter(message_repo=message_repo, vector_repo=vector_repo, fact_repo=fact_repo)
    message = writer.write(
        session_id="s1",
        user_id="u1",
        role="user",
        content="我今天在复习数学",
        emotion_score=0.0,
    )
    result = vector_repo.search(query="复习数学", user_id="u1", limit=3, min_score=0.0)
    assert any(item.message_id == message.message_id for item in result)
    hit = next(item for item in result if item.message_id == message.message_id)
    assert "final_score" in hit.retrieval_meta
    assert "days_since_access" in hit.retrieval_meta
    assert "decayed_heat" in hit.retrieval_meta
    row = store.conn.execute(
        "SELECT heat_score, access_count, last_accessed_at FROM vector_index WHERE message_id = ?",
        (message.message_id,),
    ).fetchone()
    assert row is not None
    assert float(row["heat_score"]) > 0.0
    assert int(row["access_count"]) >= 1
    assert str(row["last_accessed_at"]).strip() != ""


def test_decayed_heat_still_affects_ranking(tmp_path) -> None:
    old_weight = settings.retrieval.heat_boost_weight
    old_decay = settings.retrieval.heat_decay_per_day
    try:
        settings.retrieval.heat_boost_weight = 0.4
        settings.retrieval.heat_decay_per_day = 0.05
        store = SQLiteStore(str(tmp_path / "heat_rank.db"))
        message_repo = SQLiteMessageRepo(store)
        vector_repo = SQLiteVectorRepo(store)
        fact_repo = SQLiteFactRepo(store)
        writer = MemoryWriter(message_repo=message_repo, vector_repo=vector_repo, fact_repo=fact_repo)
        hot = writer.write(
            session_id="s_hot",
            user_id="u1",
            role="user",
            content="项目复盘记录",
            emotion_score=0.0,
        )
        normal = writer.write(
            session_id="s_norm",
            user_id="u1",
            role="user",
            content="项目复盘记录",
            emotion_score=0.0,
        )
        old_time = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        store.conn.execute(
            "UPDATE vector_index SET heat_score = ?, access_count = ?, last_accessed_at = ? WHERE message_id = ?",
            (0.6, 5, old_time, hot.message_id),
        )
        store.conn.execute(
            "UPDATE vector_index SET heat_score = ?, access_count = ?, last_accessed_at = ? WHERE message_id = ?",
            (0.0, 0, old_time, normal.message_id),
        )
        store.conn.commit()
        ranked = vector_repo.search(query="项目复盘记录", user_id="u1", limit=2, min_score=0.0)
        assert ranked[0].message_id == hot.message_id
        assert float(ranked[0].retrieval_meta.get("heat_boost", 0.0)) > float(
            ranked[1].retrieval_meta.get("heat_boost", 0.0)
        )
    finally:
        settings.retrieval.heat_boost_weight = old_weight
        settings.retrieval.heat_decay_per_day = old_decay
