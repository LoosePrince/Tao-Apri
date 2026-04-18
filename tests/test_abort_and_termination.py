from app.core.config import settings
from app.core.metrics import MetricsRegistry
from app.domain.conversation_scope import ConversationScope
from app.domain.group_conversation_hints import GroupConversationHints
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

    def _executor(
        scope: ConversationScope,
        batch: list[str],
        abort_requested: bool,
        nickname: str | None,
        source_message_id: str | None,
        attachments: list[dict[str, object]],
        _hints: GroupConversationHints,
        _window_round_id: int,
    ) -> ChatResult:
        del scope, abort_requested, nickname, source_message_id, attachments, _hints, _window_round_id
        calls.append(batch)
        return ChatResult(session_id="s1", reply="ok", session_emotion=0.1, global_emotion=0.2)

    mgr = ConversationWindowManager(batch_executor=_executor, metrics=MetricsRegistry())
    mgr.start()
    try:
        scope = ConversationScope.private(platform="test", user_id="u1")
        res = mgr.process_user_message(scope=scope, user_message="算了，重新来")
        assert res.reply == "ok"
        assert calls and calls[0] == ["算了，重新来"]
    finally:
        mgr.stop()
        settings.rhythm.silence_seconds = old_silence
        settings.rhythm.wait_timeout_seconds = old_wait
        settings.rhythm.terminate_keywords = old_terminate
