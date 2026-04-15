from app.domain.services.emotion_engine import EmotionEngine
from app.repos.sqlite_repo import SQLiteEmotionStateRepo, SQLiteStore


def test_global_emotion_persistence(tmp_path) -> None:
    db_path = tmp_path / "emotion.db"
    store = SQLiteStore(str(db_path))
    repo = SQLiteEmotionStateRepo(store)

    engine_1 = EmotionEngine(decay=0.05, gain=1.0, state_repo=repo)
    state_1 = engine_1.update(session_last_emotion=0.0, message_score=0.6)
    assert state_1.global_emotion > 0

    engine_2 = EmotionEngine(decay=0.05, gain=1.0, state_repo=repo)
    assert engine_2.global_emotion == state_1.global_emotion
