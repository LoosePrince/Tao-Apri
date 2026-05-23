import threading
import time

import pytest

from app.core.config import settings
from app.core.metrics import MetricsRegistry
from app.domain.conversation_scope import ConversationScope
from app.domain.group_conversation_hints import GroupConversationHints
from app.services.chat_orchestrator import ChatResult
from app.services.conversation_window_manager import ConversationWindowManager


def test_window_lock_and_handover() -> None:
    old_silence = settings.rhythm.silence_seconds
    old_wait = settings.rhythm.wait_timeout_seconds
    settings.rhythm.silence_seconds = 0.05
    settings.rhythm.wait_timeout_seconds = 2.0
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
        result = mgr.process_user_message(scope=scope, user_message="你好")
        assert result.reply == "ok"
        assert calls and calls[0] == ["你好"]
    finally:
        mgr.stop()
        settings.rhythm.silence_seconds = old_silence
        settings.rhythm.wait_timeout_seconds = old_wait


def test_window_disable_max_think_does_not_fake_timeout_on_slow_batch() -> None:
    """Previously, max-think off still used a derived finite wait and could return the
    placeholder timeout reply while the executor kept running."""
    old_silence = settings.rhythm.silence_seconds
    old_wait = settings.rhythm.wait_timeout_seconds
    old_cooldown = settings.rhythm.cooldown_seconds
    old_max_think = settings.rhythm.enable_max_think_seconds

    settings.rhythm.silence_seconds = 0.05
    settings.rhythm.wait_timeout_seconds = 12.0
    settings.rhythm.cooldown_seconds = 0.0
    settings.rhythm.enable_max_think_seconds = False

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
        del scope, abort_requested, nickname, source_message_id, attachments, _hints, _window_round_id, batch
        # Old inner budget was wait - silence - cooldown - 0.5 ≈ 11.45s; stay above that.
        time.sleep(11.6)
        return ChatResult(session_id="slow-ok", reply="done", session_emotion=0.0, global_emotion=0.0)

    mgr = ConversationWindowManager(batch_executor=_executor, metrics=MetricsRegistry())
    mgr.start()
    try:
        scope = ConversationScope.private(platform="test", user_id="u-slow-maxthink-off")
        result = mgr.process_user_message(scope=scope, user_message="ping")
        assert result.reply == "done"
        assert result.session_id == "slow-ok"
    finally:
        mgr.stop()
        settings.rhythm.silence_seconds = old_silence
        settings.rhythm.wait_timeout_seconds = old_wait
        settings.rhythm.cooldown_seconds = old_cooldown
        settings.rhythm.enable_max_think_seconds = old_max_think


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
        n["v"] += 1
        if n["v"] == 1:
            raise RuntimeError("boom")
        return ChatResult(session_id="s2", reply="ok2", session_emotion=0.1, global_emotion=0.2)

    mgr = ConversationWindowManager(batch_executor=_executor, metrics=MetricsRegistry())
    mgr.start()
    try:
        scope = ConversationScope.private(platform="test", user_id="u1")
        with pytest.raises(RuntimeError, match="boom"):
            mgr.process_user_message(scope=scope, user_message="m1")

        result = mgr.process_user_message(scope=scope, user_message="m2")
        assert result.reply == "ok2"
        assert calls and calls[0] == ["m1"]
        assert calls and calls[1] == ["m2"]
    finally:
        mgr.stop()
        settings.rhythm.silence_seconds = old_silence
        settings.rhythm.wait_timeout_seconds = old_wait
        settings.rhythm.cooldown_seconds = old_cooldown
        settings.rhythm.enable_max_think_seconds = old_max_think


def test_window_idle_state_cleanup() -> None:
    old_silence = settings.rhythm.silence_seconds
    old_wait = settings.rhythm.wait_timeout_seconds
    old_cooldown = settings.rhythm.cooldown_seconds
    old_ttl = settings.rhythm.window_state_ttl_seconds
    old_cleanup = settings.rhythm.window_cleanup_interval_seconds

    settings.rhythm.silence_seconds = 0.05
    settings.rhythm.wait_timeout_seconds = 1.0
    settings.rhythm.cooldown_seconds = 0.0
    settings.rhythm.window_state_ttl_seconds = 0.1
    settings.rhythm.window_cleanup_interval_seconds = 0.05

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
        del scope, batch, abort_requested, nickname, source_message_id, attachments, _hints, _window_round_id
        return ChatResult(session_id="s-clean", reply="ok", session_emotion=0.0, global_emotion=0.0)

    mgr = ConversationWindowManager(batch_executor=_executor, metrics=MetricsRegistry())
    mgr.start()
    try:
        scope = ConversationScope.private(platform="test", user_id="cleanup-user")
        result = mgr.process_user_message(scope=scope, user_message="hello")
        assert result.reply == "ok"
        time.sleep(0.35)
        assert not mgr._states
    finally:
        mgr.stop()
        settings.rhythm.silence_seconds = old_silence
        settings.rhythm.wait_timeout_seconds = old_wait
        settings.rhythm.cooldown_seconds = old_cooldown
        settings.rhythm.window_state_ttl_seconds = old_ttl
        settings.rhythm.window_cleanup_interval_seconds = old_cleanup


def test_window_batch_worker_limit_bounds_parallel_scopes() -> None:
    old_silence = settings.rhythm.silence_seconds
    old_wait = settings.rhythm.wait_timeout_seconds
    old_cooldown = settings.rhythm.cooldown_seconds
    old_max_think = settings.rhythm.enable_max_think_seconds
    old_workers = settings.rhythm.window_batch_worker_count

    settings.rhythm.silence_seconds = 0.01
    settings.rhythm.wait_timeout_seconds = 2.0
    settings.rhythm.cooldown_seconds = 0.0
    settings.rhythm.enable_max_think_seconds = False
    settings.rhythm.window_batch_worker_count = 1

    gate = threading.Event()
    seen = threading.Event()
    lock = threading.Lock()
    inflight = {"count": 0, "max": 0}

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
        del scope, batch, abort_requested, nickname, source_message_id, attachments, _hints, _window_round_id
        with lock:
            inflight["count"] += 1
            inflight["max"] = max(inflight["max"], inflight["count"])
        seen.set()
        gate.wait(timeout=1.0)
        with lock:
            inflight["count"] -= 1
        return ChatResult(session_id="s-limit", reply="ok", session_emotion=0.0, global_emotion=0.0)

    mgr = ConversationWindowManager(batch_executor=_executor, metrics=MetricsRegistry())
    mgr.start()
    results: list[str] = []

    def _send(user_id: str) -> None:
        scope = ConversationScope.private(platform="test", user_id=user_id)
        result = mgr.process_user_message(scope=scope, user_message=f"msg-{user_id}")
        results.append(result.reply)

    t1 = threading.Thread(target=_send, args=("u-a",), daemon=True)
    t2 = threading.Thread(target=_send, args=("u-b",), daemon=True)
    try:
        t1.start()
        assert seen.wait(timeout=1.0)
        t2.start()
        time.sleep(0.15)
        with lock:
            assert inflight["max"] == 1
        gate.set()
        t1.join(timeout=2.0)
        t2.join(timeout=2.0)
        assert sorted(results) == ["ok", "ok"]
    finally:
        gate.set()
        mgr.stop()
        settings.rhythm.silence_seconds = old_silence
        settings.rhythm.wait_timeout_seconds = old_wait
        settings.rhythm.cooldown_seconds = old_cooldown
        settings.rhythm.enable_max_think_seconds = old_max_think
        settings.rhythm.window_batch_worker_count = old_workers
