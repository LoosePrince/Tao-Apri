from app.core.config import settings
from app.core.metrics import MetricsRegistry
from app.services.chat_orchestrator import ChatResult
from app.services.conversation_window_manager import ConversationWindowManager


def test_termination_keyword_triggers_new_round() -> None:
    old_silence = settings.rhythm.silence_seconds
    old_wait = settings.rhythm.wait_timeout_seconds
    old_terminate = settings.rhythm.terminate_keywords
    settings.rhythm.silence_seconds = 0.05
    settings.rhythm.wait_timeout_seconds = 2.0
    settings.rhythm.terminate_keywords = ["算了"]
    calls: list[list[str]] = []

    def _executor(user_id: str, batch: list[str], abort_requested: bool, nickname: str | None) -> ChatResult:
        del user_id, abort_requested, nickname
        calls.append(batch)
        return ChatResult(session_id="s1", reply="ok", session_emotion=0.1, global_emotion=0.2)

    mgr = ConversationWindowManager(batch_executor=_executor, metrics=MetricsRegistry())
    mgr.start()
    try:
        res = mgr.process_user_message(user_id="u1", user_message="算了，重新来")
        assert res.reply == "ok"
        assert calls and calls[0] == ["算了，重新来"]
    finally:
        mgr.stop()
        settings.rhythm.silence_seconds = old_silence
        settings.rhythm.wait_timeout_seconds = old_wait
        settings.rhythm.terminate_keywords = old_terminate
