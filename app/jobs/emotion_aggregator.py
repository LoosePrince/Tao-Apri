from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.domain.services.emotion_engine import EmotionEngine
from app.repos.interfaces import MessageRepo


@dataclass(slots=True)
class EmotionSnapshot:
    window_start: datetime
    window_end: datetime
    avg_input_score: float
    global_emotion: float


class EmotionAggregatorJob:
    def __init__(self, message_repo: MessageRepo, emotion_engine: EmotionEngine) -> None:
        self.message_repo = message_repo
        self.emotion_engine = emotion_engine

    def run(self, window_minutes: int = 30) -> EmotionSnapshot:
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(minutes=window_minutes)
        messages = [
            item
            for item in self.message_repo.list_all(limit=500)
            if item.created_at >= start_time and item.role == "user"
        ]
        if messages:
            avg_input = sum(item.emotion_score for item in messages) / len(messages)
        else:
            avg_input = 0.0
        # Use update with zero session context to apply decay+gain on global channel.
        state = self.emotion_engine.update(session_last_emotion=0.0, message_score=avg_input)
        return EmotionSnapshot(
            window_start=start_time,
            window_end=end_time,
            avg_input_score=avg_input,
            global_emotion=state.global_emotion,
        )
