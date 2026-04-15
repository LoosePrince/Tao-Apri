from datetime import datetime, timedelta, timezone

from app.domain.services.emotion_engine import EmotionEngine
from app.jobs.emotion_aggregator import EmotionAggregatorJob
from app.jobs.periodic_scheduler import PeriodicScheduler
from app.domain.services.memory_writer import MemoryWriter
from app.repos.sqlite_repo import SQLiteEmotionStateRepo, SQLiteFactRepo, SQLiteMessageRepo, SQLiteStore, SQLiteVectorRepo


def test_periodic_scheduler_run_once_executes_registered_jobs() -> None:
    state = {"count": 0}
    scheduler = PeriodicScheduler(enabled=True)

    def job() -> None:
        state["count"] += 1

    scheduler.add_job(name="job1", interval_seconds=1.0, job=job)
    scheduler.add_job(name="job2", interval_seconds=1.0, job=job)
    scheduler.run_once()
    assert state["count"] == 2


def test_vector_maintenance_decays_heat_and_updates_rows(tmp_path) -> None:
    store = SQLiteStore(str(tmp_path / "maintenance.db"))
    message_repo = SQLiteMessageRepo(store)
    vector_repo = SQLiteVectorRepo(store)
    fact_repo = SQLiteFactRepo(store)
    writer = MemoryWriter(message_repo=message_repo, vector_repo=vector_repo, fact_repo=fact_repo)
    message = writer.write(
        session_id="s1",
        user_id="u1",
        role="user",
        content="维护任务测试",
        emotion_score=0.0,
    )
    old_time = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    store.conn.execute(
        "UPDATE vector_index SET heat_score = ?, last_accessed_at = ? WHERE message_id = ?",
        (0.9, old_time, message.message_id),
    )
    store.conn.commit()
    result = vector_repo.run_maintenance()
    assert result["updated"] >= 1
    row = store.conn.execute(
        "SELECT heat_score FROM vector_index WHERE message_id = ?",
        (message.message_id,),
    ).fetchone()
    assert row is not None
    assert float(row["heat_score"]) < 0.9


def test_emotion_aggregator_job_runs_with_recent_messages(tmp_path) -> None:
    store = SQLiteStore(str(tmp_path / "emotion_job.db"))
    message_repo = SQLiteMessageRepo(store)
    vector_repo = SQLiteVectorRepo(store)
    fact_repo = SQLiteFactRepo(store)
    emotion_state_repo = SQLiteEmotionStateRepo(store)
    writer = MemoryWriter(message_repo=message_repo, vector_repo=vector_repo, fact_repo=fact_repo)
    writer.write(session_id="s1", user_id="u1", role="user", content="今天挺开心", emotion_score=0.6)
    engine = EmotionEngine(state_repo=emotion_state_repo)
    job = EmotionAggregatorJob(message_repo=message_repo, emotion_engine=engine)
    snapshot = job.run(window_minutes=60)
    assert snapshot.avg_input_score >= 0.0
