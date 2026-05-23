from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
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
from app.services.window_delivery_timeout import mark_late_assistant_delivery

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _Waiter:
    target_round: int
    event: threading.Event
    holder: dict[str, ChatResult | Exception | None]


@dataclass(slots=True)
class _DetachedBatch:
    future: Future[ChatResult]
    scope_id: str
    round_id: int


@dataclass(slots=True)
class WindowState:
    mode: str = "LISTENING"
    q1: list[str] = field(default_factory=list)
    q2: list[str] = field(default_factory=list)
    last_scope: ConversationScope | None = None
    last_nickname: str | None = None
    last_source_message_id: str | None = None
    last_attachments: list[dict[str, object]] = field(default_factory=list)
    group_bot_mentioned_or: bool = False
    group_whitelist_autonomous: bool = False
    silence_deadline: float | None = None
    cooldown_until: float = 0.0
    active_round: int = 0
    completed_round: int = 0
    waiters: list[_Waiter] = field(default_factory=list)
    abort_requested: bool = False
    pending_batch: list[str] = field(default_factory=list)
    pending_group_hints: GroupConversationHints | None = None
    inflight_future: Future[ChatResult] | None = None
    inflight_started_at: float | None = None
    inflight_round_id: int | None = None
    inflight_scope: ConversationScope | None = None
    last_touched_at: float = field(default_factory=time.monotonic)
    lock: threading.RLock = field(default_factory=threading.RLock)


class ConversationWindowManager:
    def __init__(
        self,
        *,
        batch_executor: Callable[..., ChatResult],
        metrics: MetricsRegistry,
    ) -> None:
        self.batch_executor = batch_executor
        self.metrics = metrics
        self._states: dict[str, WindowState] = {}
        self._states_lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._executor: ThreadPoolExecutor | None = None
        self._max_batch_workers = max(1, settings.rhythm.window_batch_worker_count)
        self._running_batch_count = 0
        self._detached_batches: list[_DetachedBatch] = []
        self._last_cleanup_at = 0.0

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._max_batch_workers = max(1, settings.rhythm.window_batch_worker_count)
        self._executor = ThreadPoolExecutor(
            max_workers=self._max_batch_workers,
            thread_name_prefix="window-batch",
        )
        self._last_cleanup_at = time.monotonic()
        self._thread = threading.Thread(target=self._loop, name="conversation-window-manager", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread is None:
            return
        self._stop_event.set()
        self._thread.join(timeout=2.0)
        self._thread = None
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=False)
            self._executor = None
        self._detached_batches.clear()
        self._running_batch_count = 0

    def process_user_message(
        self,
        *,
        scope: ConversationScope,
        user_message: str,
        nickname: str | None = None,
        source_message_id: str | None = None,
        attachments: list[dict[str, object]] | None = None,
        group_bot_mentioned: bool | None = None,
        group_allow_autonomous: bool | None = None,
    ) -> ChatResult:
        started = time.monotonic()
        try:
            result = self._enqueue_and_wait(
                scope=scope,
                user_message=user_message,
                nickname=nickname,
                source_message_id=source_message_id,
                attachments=attachments,
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
        with self._states_lock:
            state = self._states.get(scope_id)
            if state is None:
                state = WindowState()
                self._states[scope_id] = state
            return state

    def _enqueue_and_wait(
        self,
        *,
        scope: ConversationScope,
        user_message: str,
        nickname: str | None,
        source_message_id: str | None,
        attachments: list[dict[str, object]] | None,
        group_bot_mentioned: bool | None = None,
        group_allow_autonomous: bool | None = None,
    ) -> ChatResult:
        state = self._state_for(scope.scope_id)
        now = time.monotonic()
        with state.lock:
            state.last_touched_at = now
            state.last_scope = scope
            if nickname:
                nick = nickname.strip()
                if nick:
                    state.last_nickname = nick
            if source_message_id and source_message_id.strip():
                state.last_source_message_id = source_message_id.strip()
            if attachments:
                state.last_attachments = list(attachments)
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
                base_deadline = max(
                    now + settings.rhythm.silence_seconds,
                    state.cooldown_until + settings.rhythm.silence_seconds,
                )
                if settings.rhythm.enable_max_think_seconds:
                    execution_budget = settings.rhythm.max_think_seconds
                else:
                    execution_budget = max(
                        1.0,
                        settings.rhythm.wait_timeout_seconds
                        - settings.rhythm.silence_seconds
                        - settings.rhythm.cooldown_seconds
                        - 0.5,
                    )
                hard_limit = now + max(
                    0.0,
                    settings.rhythm.wait_timeout_seconds - execution_budget - 0.5,
                )
                state.silence_deadline = min(base_deadline, hard_limit)
            waiter = _Waiter(target_round=target_round, event=threading.Event(), holder={"result": None})
            state.waiters.append(waiter)
        if not waiter.event.wait(timeout=settings.rhythm.wait_timeout_seconds):
            mark_late_assistant_delivery(scope.scope_id, waiter.target_round)
            with state.lock:
                state.waiters = [item for item in state.waiters if item is not waiter]
                state.last_touched_at = time.monotonic()
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
                self._drain_detached_batches()
                with self._states_lock:
                    items = list(self._states.items())
                for scope_id, state in items:
                    try:
                        self._tick_state(scope_id, state, now)
                    except Exception:
                        logger.exception("ConversationWindowManager loop error | scope_id=%s", scope_id)
                cleanup_interval = max(0.1, settings.rhythm.window_cleanup_interval_seconds)
                if now - self._last_cleanup_at >= cleanup_interval:
                    self._cleanup_idle_states(now)
                    self._last_cleanup_at = now
            except Exception:
                logger.exception("ConversationWindowManager loop fatal error")
            time.sleep(0.05)

    def _tick_state(self, scope_id: str, state: WindowState, now: float) -> None:
        completed_future: Future[ChatResult] | None = None
        completed_round_id: int | None = None
        timed_out_scope: ConversationScope | None = None
        timed_out_round_id: int | None = None
        dispatch_error: Exception | None = None
        dispatch_error_round_id: int | None = None

        with state.lock:
            if state.inflight_future is not None:
                future = state.inflight_future
                if future.done():
                    completed_future = future
                    completed_round_id = state.inflight_round_id or state.active_round
                    self._decrement_running_batches()
                    self._clear_inflight_locked(state)
                    state.last_touched_at = now
                elif (
                    settings.rhythm.enable_max_think_seconds
                    and state.inflight_started_at is not None
                    and now - state.inflight_started_at >= settings.rhythm.max_think_seconds
                ):
                    timed_out_scope = state.inflight_scope or state.last_scope
                    timed_out_round_id = state.inflight_round_id or state.active_round
                    self._detach_running_batch(state.inflight_future, scope_id, timed_out_round_id)
                    self._clear_inflight_locked(state)
                    state.last_touched_at = now
            elif (
                state.mode == "LISTENING"
                and state.q1
                and state.silence_deadline is not None
                and now >= state.silence_deadline
                and now >= state.cooldown_until
            ):
                state.pending_batch = list(state.q1)
                state.pending_group_hints = GroupConversationHints(
                    bot_mentioned=state.group_bot_mentioned_or,
                    allow_autonomous_without_mention=state.group_whitelist_autonomous,
                )
                state.group_bot_mentioned_or = False
                state.group_whitelist_autonomous = False
                state.q1.clear()
                state.mode = "LOCKED"
                state.active_round = state.completed_round + 1
                state.silence_deadline = None
                state.last_touched_at = now
                self.metrics.inc("lock_trigger_count")

            if (
                state.mode == "LOCKED"
                and state.pending_batch
                and state.inflight_future is None
                and self._running_batch_count < self._max_batch_workers
            ):
                scope_for_batch = state.last_scope
                round_id = state.active_round
                if scope_for_batch is None:
                    dispatch_error = RuntimeError("Missing ConversationScope for batch execution")
                    dispatch_error_round_id = round_id
                    state.pending_batch = []
                    state.pending_group_hints = None
                    state.mode = "LISTENING"
                    state.silence_deadline = None
                    state.last_touched_at = now
                else:
                    try:
                        future = self._submit_batch_locked(state, scope_for_batch, round_id)
                    except Exception as exc:
                        dispatch_error = exc
                        dispatch_error_round_id = round_id
                        state.pending_batch = []
                        state.pending_group_hints = None
                        state.mode = "LISTENING"
                        state.silence_deadline = None
                        state.last_touched_at = now
                    else:
                        if future is None:
                            dispatch_error = RuntimeError("Conversation window executor is unavailable")
                            dispatch_error_round_id = round_id
                            state.pending_batch = []
                            state.pending_group_hints = None
                            state.mode = "LISTENING"
                            state.silence_deadline = None
                            state.last_touched_at = now
                        else:
                            state.inflight_future = future
                            state.inflight_started_at = now
                            state.inflight_round_id = round_id
                            state.inflight_scope = scope_for_batch
                            state.pending_batch = []
                            state.pending_group_hints = None
                            state.mode = "RESPONDING"
                            state.last_touched_at = now

        if completed_future is not None and completed_round_id is not None:
            try:
                result = completed_future.result()
            except Exception as future_exc:
                self._finalize_round_exception(scope_id, completed_round_id, future_exc)
            else:
                self._finalize_round_result(scope_id, completed_round_id, result)
            return

        if timed_out_scope is not None and timed_out_round_id is not None:
            self.metrics.inc("max_think_timeout_count")
            mark_late_assistant_delivery(timed_out_scope.scope_id, timed_out_round_id)
            self._finalize_round_result(
                scope_id,
                timed_out_round_id,
                self._fallback_timeout_result(scope_id=scope_id, scope=timed_out_scope, round_id=timed_out_round_id),
            )
            return

        if dispatch_error is not None and dispatch_error_round_id is not None:
            self._finalize_round_exception(scope_id, dispatch_error_round_id, dispatch_error)

    def _submit_batch_locked(
        self,
        state: WindowState,
        scope_for_batch: ConversationScope,
        round_id: int,
    ) -> Future[ChatResult] | None:
        if self._executor is None:
            return None
        batch = list(state.pending_batch)
        group_hints = state.pending_group_hints or GroupConversationHints(
            bot_mentioned=False,
            allow_autonomous_without_mention=False,
        )
        abort_requested = state.abort_requested
        nickname_for_batch = state.last_nickname
        source_message_id_for_batch = state.last_source_message_id
        attachments_for_batch = list(state.last_attachments)
        future = self._executor.submit(
            self.batch_executor,
            scope_for_batch,
            batch,
            abort_requested,
            nickname_for_batch,
            source_message_id_for_batch,
            attachments_for_batch,
            group_hints,
            round_id,
        )
        self._running_batch_count += 1
        return future

    @staticmethod
    def _fallback_timeout_result(*, scope_id: str, scope: ConversationScope | None, round_id: int) -> ChatResult:
        scope_label = scope.scope_id if scope is not None else scope_id
        return ChatResult(
            session_id=f"timeout-{scope_label}-{round_id}",
            reply="（本轮思考超时，已中断当前回答。请继续发送，我会基于新一轮继续处理。）",
            session_emotion=0.0,
            global_emotion=0.0,
        )

    def _finalize_round_result(self, scope_id: str, round_id: int, result: ChatResult) -> None:
        state = self._state_for(scope_id)
        with state.lock:
            state.last_touched_at = time.monotonic()
            if state.abort_requested:
                self.metrics.inc("abort_discard_count")
            else:
                for waiter in state.waiters:
                    if waiter.target_round == round_id:
                        waiter.holder["result"] = result
                        waiter.event.set()
            state.waiters = [waiter for waiter in state.waiters if not waiter.event.is_set()]
            self._advance_after_round_locked(state, round_id)

    def _finalize_round_exception(self, scope_id: str, round_id: int, exc: Exception) -> None:
        state = self._state_for(scope_id)
        with state.lock:
            state.last_touched_at = time.monotonic()
            for waiter in state.waiters:
                if waiter.target_round == round_id:
                    waiter.holder["result"] = exc
                    waiter.event.set()
            state.waiters = [waiter for waiter in state.waiters if not waiter.event.is_set()]
            self._advance_after_round_locked(state, round_id)

    @staticmethod
    def _advance_after_round_locked(state: WindowState, round_id: int) -> None:
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

    @staticmethod
    def _clear_inflight_locked(state: WindowState) -> None:
        state.inflight_future = None
        state.inflight_started_at = None
        state.inflight_round_id = None
        state.inflight_scope = None

    def _detach_running_batch(self, future: Future[ChatResult], scope_id: str, round_id: int) -> None:
        self._detached_batches.append(_DetachedBatch(future=future, scope_id=scope_id, round_id=round_id))

    def _drain_detached_batches(self) -> None:
        if not self._detached_batches:
            return
        remaining: list[_DetachedBatch] = []
        for item in self._detached_batches:
            if not item.future.done():
                remaining.append(item)
                continue
            self._decrement_running_batches()
            try:
                item.future.result()
            except Exception as exc:
                logger.warning(
                    "Timed-out batch finished with error | scope_id=%s | round_id=%s | err=%s",
                    item.scope_id,
                    item.round_id,
                    exc,
                )
        self._detached_batches = remaining

    def _cleanup_idle_states(self, now: float) -> None:
        ttl_seconds = max(0.1, settings.rhythm.window_state_ttl_seconds)
        max_active_scopes = max(1, settings.rhythm.window_max_active_scopes)

        with self._states_lock:
            items = list(self._states.items())
            idle_entries: list[tuple[float, str]] = []
            removable: list[str] = []
            for scope_id, state in items:
                with state.lock:
                    is_idle = self._is_state_idle_locked(state)
                    last_touched_at = state.last_touched_at
                if not is_idle:
                    continue
                idle_entries.append((last_touched_at, scope_id))
                if now - last_touched_at >= ttl_seconds:
                    removable.append(scope_id)
            for scope_id in removable:
                state = self._states.get(scope_id)
                if state is None:
                    continue
                with state.lock:
                    if self._is_state_idle_locked(state) and now - state.last_touched_at >= ttl_seconds:
                        self._states.pop(scope_id, None)

            overflow = len(self._states) - max_active_scopes
            if overflow <= 0:
                return
            idle_entries.sort(key=lambda item: item[0])
            removed = 0
            for _, scope_id in idle_entries:
                if removed >= overflow:
                    break
                state = self._states.get(scope_id)
                if state is None:
                    continue
                with state.lock:
                    if self._is_state_idle_locked(state):
                        self._states.pop(scope_id, None)
                        removed += 1

    @staticmethod
    def _is_state_idle_locked(state: WindowState) -> bool:
        return (
            state.mode == "LISTENING"
            and not state.q1
            and not state.q2
            and not state.waiters
            and not state.pending_batch
            and state.inflight_future is None
            and state.silence_deadline is None
        )

    def _decrement_running_batches(self) -> None:
        if self._running_batch_count > 0:
            self._running_batch_count -= 1