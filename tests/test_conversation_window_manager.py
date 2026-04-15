from app.core.config import settings
from app.core.metrics import MetricsRegistry
from app.services.chat_orchestrator import ChatResult
from app.services.conversation_window_manager import ConversationWindowManager


def test_window_lock_and_handover() -> None:
    old_silence = settings.rhythm.silence_seconds
    old_wait = settings.rhythm.wait_timeout_seconds
    settings.rhythm.silence_seconds = 0.05
    settings.rhythm.wait_timeout_seconds = 2.0
    calls: list[list[str]] = []

    def _executor(user_id: str, batch: list[str], abort_requested: bool) -> ChatResult:
        del user_id, abort_requested
        calls.append(batch)
        return ChatResult(session_id="s1", reply="ok", session_emotion=0.1, global_emotion=0.2)

    mgr = ConversationWindowManager(batch_executor=_executor, metrics=MetricsRegistry())
    mgr.start()
    try:
        result = mgr.process_user_message(user_id="u1", user_message="你好")
        assert result.reply == "ok"
        assert calls and calls[0] == ["你好"]
    finally:
        mgr.stop()
        settings.rhythm.silence_seconds = old_silence
        settings.rhythm.wait_timeout_seconds = old_wait
