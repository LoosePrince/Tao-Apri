from collections import deque
from dataclasses import dataclass
from statistics import fmean

from app.core.rule_lexicons import emotion_scoring_lexicon
from app.repos.interfaces import EmotionStateRepo


@dataclass(slots=True)
class EmotionState:
    session_emotion: float
    global_emotion: float


class EmotionEngine:
    def __init__(
        self,
        decay: float = 0.05,
        gain: float = 0.8,
        max_history: int = 1000,
        state_repo: EmotionStateRepo | None = None,
    ) -> None:
        self.decay = decay
        self.gain = gain
        self.state_repo = state_repo
        self.global_emotion = state_repo.get_global_emotion() if state_repo else 0.0
        self._history: deque[float] = deque(maxlen=max_history)

    def score_message(self, text: str) -> float:
        normalized = text.lower()
        positive, negative, step = emotion_scoring_lexicon()
        score = 0.0
        for word in positive:
            if word in normalized:
                score += step
        for word in negative:
            if word in normalized:
                score -= step
        return max(-1.0, min(1.0, score))

    def update(self, session_last_emotion: float, message_score: float) -> EmotionState:
        self._history.append(message_score)
        window_avg = fmean(self._history) if self._history else 0.0
        next_global = self.global_emotion * (1.0 - self.decay) + window_avg * self.gain
        self.global_emotion = max(-1.0, min(1.0, next_global))
        if self.state_repo:
            self.state_repo.set_global_emotion(self.global_emotion)
        session_emotion = max(-1.0, min(1.0, session_last_emotion * 0.6 + message_score * 0.4 + self.global_emotion * 0.3))
        return EmotionState(session_emotion=session_emotion, global_emotion=self.global_emotion)
