from __future__ import annotations

from dataclasses import dataclass, field
import logging
import threading
import time
from typing import Callable

from app.core.config import settings
from app.core.metrics import MetricsRegistry
from app.domain.conversation_scope import ConversationScope
from app.domain.group_conversation_hints import GroupConversationHints
from app.services.chat_orchestrator import ChatResult

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _Waiter:
    target_round: int
    event: threading.Event
    holder: dict[str, ChatResult | Exception | None]


@dataclass(slots=True)
class WindowState:
    mode: str = "LISTENING"
    q1: list[str] = field(default_factory=list)
    q2: list[str] = field(default_factory=list)
    last_scope: ConversationScope | None = None
    last_nickname: str | None = None
    group_bot_mentioned_or: bool = False
    group_whitelist_autonomous: bool = False
    silence_deadline: float | None = None
    cooldown_until: float = 0.0
    active_round: int = 0
    completed_round: int = 0
    waiters: list[_Waiter] = field(default_factory=list)
    abort_requested: bool = False
    batch_scheduled: bool = False
    lock: threading.RLock = field(default_factory=threading.RLock)


class ConversationWindowManager:
    def __init__(
        self,
        *,
        batch_executor: Callable[[ConversationScope, list[str], bool, str | None, GroupConversationHints], ChatResult],
        metrics: MetricsRegistry,
    ) -> None:
        self.batch_executor = batch_executor
        self.metrics = metrics
        self._states: dict[str, WindowState] = {}
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, name="conversation-window-manager", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread is None:
            return
        self._stop_event.set()
        self._thread.join(timeout=2.0)
        self._thread = None

    def process_user_message(
        self,
        *,
        scope: ConversationScope,
        user_message: str,
        nickname: str | None = None,
        group_bot_mentioned: bool | None = None,
        group_allow_autonomous: bool | None = None,
    ) -> ChatResult:
        started = time.monotonic()
        try:
            result = self._enqueue_and_wait(
                scope=scope,
                user_message=user_message,
                nickname=nickname,
                group_bot_mentioned=group_bot_mentioned,
                group_allow_autonomous=group_allow_autonomous,
            )
            self.metrics.observe_request(latency_ms=(time.monotonic() - started) * 1000.0, is_error=False)
            return result
        except Exception:
            self.metrics.observe_request(latency_ms=(time.monotonic() - started) * 1000.0, is_error=True)
            self.metrics.inc("error_count")
            raise

    def _state_for(self, scope_id: str) -> WindowState:
        if scope_id not in self._states:
            self._states[scope_id] = WindowState()
        return self._states[scope_id]

    def _enqueue_and_wait(
        self,
        *,
        scope: ConversationScope,
        user_message: str,
        nickname: str | None,
        group_bot_mentioned: bool | None = None,
        group_allow_autonomous: bool | None = None,
    ) -> ChatResult:
        state = self._state_for(scope.scope_id)
        now = time.monotonic()
        delayed_trigger_deadline: float | None = None
        with state.lock:
            state.last_scope = scope
            if nickname:
                nick = nickname.strip()
                if nick:
                    state.last_nickname = nick
            if scope.scene_type == "group":
                if group_bot_mentioned is not None:
                    state.group_bot_mentioned_or |= bool(group_bot_mentioned)
                if group_allow_autonomous is not None:
                    state.group_whitelist_autonomous |= bool(group_allow_autonomous)
            is_terminate = (
                settings.rhythm.enable_terminate_keywords
                and any(token in user_message for token in settings.rhythm.terminate_keywords)
            )
            if is_terminate:
                self.metrics.inc("abort_count")
                if state.mode in {"LOCKED", "RESPONDING", "HANDOVER"}:
                    state.abort_requested = True
                    state.q2.clear()
                else:
                    state.q1.clear()
                    state.q2.clear()
                    state.mode = "LISTENING"
                    state.abort_requested = False
            if state.mode in {"LOCKED", "RESPONDING", "HANDOVER"}:
                state.q2.append(user_message)
                target_round = state.active_round + 1
            else:
                state.q1.append(user_message)
                target_round = state.completed_round + 1
                # Hard limit so that: (silence delay) + (max_think_seconds) <= wait_timeout_seconds,
                # otherwise the waiter may time out before the batch thread has a chance to set result.
                base_deadline = max(
                    now + settings.rhythm.silence_seconds,
                    state.cooldown_until + settings.rhythm.silence_seconds,
                )
                hard_limit = now + max(
                    0.0,
                    settings.rhythm.wait_timeout_seconds - settings.rhythm.max_think_seconds - 0.5,
                )
                state.silence_deadline = min(base_deadline, hard_limit)
                if not state.batch_scheduled:
                    state.batch_scheduled = True
                    delayed_trigger_deadline = state.silence_deadline
            waiter = _Waiter(target_round=target_round, event=threading.Event(), holder={"result": None})
            state.waiters.append(waiter)
        if delayed_trigger_deadline is not None:
            threading.Thread(
                target=self._delayed_trigger,
                args=(scope.scope_id, delayed_trigger_deadline),
                name=f"window-delayed-trigger-{scope.scope_id}",
                daemon=True,
            ).start()
        if not waiter.event.wait(timeout=settings.rhythm.wait_timeout_seconds):
            raise TimeoutError(f"Conversation window timed out for scope={scope.scope_id}")
        result = waiter.holder.get("result")
        if isinstance(result, Exception):
            raise result
        if isinstance(result, ChatResult):
            return result
        raise RuntimeError("Conversation window completed without ChatResult")

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                now = time.monotonic()
                for scope_id, state in list(self._states.items()):
                    try:
                        with state.lock:
                            if (
                                state.mode == "LISTENING"
                                and state.q1
                                and state.silence_deadline is not None
                                and now >= state.silence_deadline
                                and now >= state.cooldown_until
                            ):
                                batch = list(state.q1)
                                batch_hints = GroupConversationHints(
                                    bot_mentioned=state.group_bot_mentioned_or,
                                    allow_autonomous_without_mention=state.group_whitelist_autonomous,
                                )
                                state.group_bot_mentioned_or = False
                                state.group_whitelist_autonomous = False
                                state.q1.clear()
                                state.mode = "LOCKED"
                                state.active_round = state.completed_round + 1
                                state.silence_deadline = None
                                state.batch_scheduled = False
                                self.metrics.inc("lock_trigger_count")
                                threading.Thread(
                                    target=self._run_batch,
                                    args=(scope_id, batch, state.active_round, batch_hints),
                                    name=f"window-batch-{scope_id}",
                                    daemon=True,
                                ).start()
                    except Exception:
                        logger.exception("ConversationWindowManager loop error | scope_id=%s", scope_id)
            except Exception:
                logger.exception("ConversationWindowManager loop fatal error")
            time.sleep(0.05)

    def _delayed_trigger(self, scope_id: str, scheduled_deadline: float) -> None:
        """
        Fallback scheduler: trigger a batch when _loop() is unavailable.
        """
        # Wait until the scheduled deadline (or stop).
        while not self._stop_event.is_set():
            now = time.monotonic()
            with self._state_for(scope_id).lock:
                state = self._state_for(scope_id)
                if state.mode != "LISTENING" or not state.q1 or state.silence_deadline is None:
                    state.batch_scheduled = False
                    return
                # If deadline changed (new message or state update), stop and let next enqueue schedule.
                if state.silence_deadline != scheduled_deadline:
                    state.batch_scheduled = False
                    return
            remaining = scheduled_deadline - now
            if remaining <= 0:
                break
            time.sleep(min(0.2, remaining))

        if self._stop_event.is_set():
            with self._state_for(scope_id).lock:
                self._state_for(scope_id).batch_scheduled = False
            return

        now = time.monotonic()
        state = self._state_for(scope_id)
        with state.lock:
            if (
                state.mode != "LISTENING"
                or not state.q1
                or state.silence_deadline is None
                or state.silence_deadline != scheduled_deadline
                or now < state.silence_deadline
                or now < state.cooldown_until
            ):
                state.batch_scheduled = False
                return

            batch = list(state.q1)
            batch_hints = GroupConversationHints(
                bot_mentioned=state.group_bot_mentioned_or,
                allow_autonomous_without_mention=state.group_whitelist_autonomous,
            )
            state.group_bot_mentioned_or = False
            state.group_whitelist_autonomous = False
            state.q1.clear()
            state.mode = "LOCKED"
            state.active_round = state.completed_round + 1
            state.silence_deadline = None
            state.batch_scheduled = False
            self.metrics.inc("lock_trigger_count")

        threading.Thread(
            target=self._run_batch,
            args=(scope_id, batch, state.active_round, batch_hints),
            name=f"window-batch-{scope_id}",
            daemon=True,
        ).start()

    def _run_batch(self, scope_id: str, batch: list[str], round_id: int, group_hints: GroupConversationHints) -> None:
        state = self._state_for(scope_id)
        with state.lock:
            state.mode = "RESPONDING"
            abort_requested = state.abort_requested
            nickname_for_batch = state.last_nickname
            scope_for_batch = state.last_scope

        def _fallback_timeout_result() -> ChatResult:
            scope_label = scope_id
            if scope_for_batch is not None:
                scope_label = scope_for_batch.scope_id
            return ChatResult(
                session_id=f"timeout-{scope_label}-{round_id}",
                reply="（本轮思考超时，已中断当前回答。请继续发送，我会基于新一轮继续处理。）",
                session_emotion=0.0,
                global_emotion=0.0,
            )

        try:
            # Always execute batch in a background thread to avoid blocking longer than
            # `process_user_message`'s `wait_timeout_seconds` when downstream (LLM/IO) is slow.
            # We still respect `max_think_seconds` as the hard upper bound for producing a result.
            holder: dict[str, ChatResult | Exception | None] = {"result": None}
            finished = threading.Event()

            def _execute() -> None:
                try:
                    if scope_for_batch is None:
                        raise RuntimeError("Missing ConversationScope for batch execution")
                    holder["result"] = self.batch_executor(
                        scope_for_batch, batch, abort_requested, nickname_for_batch, group_hints
                    )
                except Exception as exc:  # pragma: no cover
                    holder["result"] = exc
                finally:
                    finished.set()

            threading.Thread(
                target=_execute,
                    name=f"window-batch-exec-{scope_id}-{round_id}",
                daemon=True,
            ).start()

            if not finished.wait(timeout=settings.rhythm.max_think_seconds):
                self.metrics.inc("max_think_timeout_count")
                result = _fallback_timeout_result()
            else:
                maybe_result = holder["result"]
                if isinstance(maybe_result, Exception):
                    raise maybe_result
                if not isinstance(maybe_result, ChatResult):
                    raise RuntimeError("Batch executor returned invalid result")
                result = maybe_result
        except Exception as exc:
            with state.lock:
                for waiter in state.waiters:
                    if waiter.target_round == round_id:
                        waiter.holder["result"] = exc
                        waiter.event.set()
                # Ensure window can recover and accept new batches after failures.
                state.waiters = [w for w in state.waiters if not w.event.is_set()]
                state.completed_round = round_id
                state.mode = "HANDOVER"
                state.q1 = list(state.q2)
                state.q2.clear()
                state.abort_requested = False
                state.cooldown_until = time.monotonic() + settings.rhythm.cooldown_seconds
                state.mode = "LISTENING"
                if state.q1:
                    state.silence_deadline = state.cooldown_until + settings.rhythm.silence_seconds
                else:
                    state.silence_deadline = None
            return

        with state.lock:
            if state.abort_requested:
                self.metrics.inc("abort_discard_count")
            else:
                for waiter in state.waiters:
                    if waiter.target_round == round_id:
                        waiter.holder["result"] = result
                        waiter.event.set()
            state.waiters = [w for w in state.waiters if not w.event.is_set()]
            state.completed_round = round_id
            state.mode = "HANDOVER"
            state.q1 = list(state.q2)
            state.q2.clear()
            state.abort_requested = False
            state.cooldown_until = time.monotonic() + settings.rhythm.cooldown_seconds
            state.mode = "LISTENING"
            if state.q1:
                state.silence_deadline = state.cooldown_until + settings.rhythm.silence_seconds
            else:
                state.silence_deadline = None
