from app.domain.services.emotion_engine import EmotionEngine


def test_emotion_engine_global_trend() -> None:
    engine = EmotionEngine(decay=0.05, gain=1.0)
    state_1 = engine.update(session_last_emotion=0.0, message_score=0.5)
    state_2 = engine.update(session_last_emotion=state_1.session_emotion, message_score=0.5)

    assert state_2.global_emotion >= state_1.global_emotion
    assert -1.0 <= state_2.session_emotion <= 1.0


def test_emotion_engine_negative_input() -> None:
    engine = EmotionEngine(decay=0.05, gain=1.0)
    state = engine.update(session_last_emotion=0.0, message_score=-0.6)
    assert state.global_emotion < 0
