from app.core.config import settings
from app.core.metrics import MetricsRegistry
from app.services.chat_orchestrator import ChatResult
from app.services.conversation_window_manager import ConversationWindowManager
import pytest


def test_window_lock_and_handover() -> None:
    old_silence = settings.rhythm.silence_seconds
    old_wait = settings.rhythm.wait_timeout_seconds
    settings.rhythm.silence_seconds = 0.05
    settings.rhythm.wait_timeout_seconds = 2.0
    calls: list[list[str]] = []

    def _executor(user_id: str, batch: list[str], abort_requested: bool, nickname: str | None) -> ChatResult:
        del user_id, abort_requested, nickname
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


def test_window_batch_executor_exception_recovers_state() -> None:
    old_silence = settings.rhythm.silence_seconds
    old_wait = settings.rhythm.wait_timeout_seconds
    old_cooldown = settings.rhythm.cooldown_seconds
    old_max_think = settings.rhythm.enable_max_think_seconds

    settings.rhythm.silence_seconds = 0.05
    settings.rhythm.wait_timeout_seconds = 1.0
    settings.rhythm.cooldown_seconds = 0.0
    settings.rhythm.enable_max_think_seconds = False

    calls: list[list[str]] = []
    n = {"v": 0}

    def _executor(user_id: str, batch: list[str], abort_requested: bool, nickname: str | None) -> ChatResult:
        del user_id, abort_requested, nickname
        calls.append(batch)
        n["v"] += 1
        if n["v"] == 1:
            raise RuntimeError("boom")
        return ChatResult(session_id="s2", reply="ok2", session_emotion=0.1, global_emotion=0.2)

    mgr = ConversationWindowManager(batch_executor=_executor, metrics=MetricsRegistry())
    mgr.start()
    try:
        with pytest.raises(RuntimeError, match="boom"):
            mgr.process_user_message(user_id="u1", user_message="m1")

        result = mgr.process_user_message(user_id="u1", user_message="m2")
        assert result.reply == "ok2"
        assert calls and calls[0] == ["m1"]
        assert calls and calls[1] == ["m2"]
    finally:
        mgr.stop()
        settings.rhythm.silence_seconds = old_silence
        settings.rhythm.wait_timeout_seconds = old_wait
        settings.rhythm.cooldown_seconds = old_cooldown
        settings.rhythm.enable_max_think_seconds = old_max_think
