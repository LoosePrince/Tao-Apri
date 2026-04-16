from __future__ import annotations

from dataclasses import dataclass, field
import logging
import threading
import time
from typing import Callable

from app.core.config import settings
from app.core.metrics import MetricsRegistry
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
    last_nickname: str | None = None
    silence_deadline: float | None = None
    cooldown_until: float = 0.0
    active_round: int = 0
    completed_round: int = 0
    waiters: list[_Waiter] = field(default_factory=list)
    abort_requested: bool = False
    lock: threading.RLock = field(default_factory=threading.RLock)


class ConversationWindowManager:
    def __init__(
        self,
        *,
        batch_executor: Callable[[str, list[str], bool, str | None], ChatResult],
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
        self, *, user_id: str, user_message: str, nickname: str | None = None
    ) -> ChatResult:
        started = time.monotonic()
        try:
            result = self._enqueue_and_wait(user_id=user_id, user_message=user_message, nickname=nickname)
            self.metrics.observe_request(latency_ms=(time.monotonic() - started) * 1000.0, is_error=False)
            return result
        except Exception:
            self.metrics.observe_request(latency_ms=(time.monotonic() - started) * 1000.0, is_error=True)
            self.metrics.inc("error_count")
            raise

    def _state_for(self, user_id: str) -> WindowState:
        if user_id not in self._states:
            self._states[user_id] = WindowState()
        return self._states[user_id]

    def _enqueue_and_wait(self, *, user_id: str, user_message: str, nickname: str | None) -> ChatResult:
        state = self._state_for(user_id)
        now = time.monotonic()
        with state.lock:
            if nickname:
                nick = nickname.strip()
                if nick:
                    state.last_nickname = nick
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
                state.silence_deadline = max(
                    now + settings.rhythm.silence_seconds,
                    state.cooldown_until + settings.rhythm.silence_seconds,
                )
            waiter = _Waiter(target_round=target_round, event=threading.Event(), holder={"result": None})
            state.waiters.append(waiter)
        if not waiter.event.wait(timeout=settings.rhythm.wait_timeout_seconds):
            raise TimeoutError(f"Conversation window timed out for user={user_id}")
        result = waiter.holder.get("result")
        if isinstance(result, Exception):
            raise result
        if isinstance(result, ChatResult):
            return result
        raise RuntimeError("Conversation window completed without ChatResult")

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            now = time.monotonic()
            for user_id, state in list(self._states.items()):
                with state.lock:
                    if (
                        state.mode == "LISTENING"
                        and state.q1
                        and state.silence_deadline is not None
                        and now >= state.silence_deadline
                        and now >= state.cooldown_until
                    ):
                        batch = list(state.q1)
                        state.q1.clear()
                        state.mode = "LOCKED"
                        state.active_round = state.completed_round + 1
                        state.silence_deadline = None
                        self.metrics.inc("lock_trigger_count")
                        threading.Thread(
                            target=self._run_batch,
                            args=(user_id, batch, state.active_round),
                            name=f"window-batch-{user_id}",
                            daemon=True,
                        ).start()
            time.sleep(0.05)

    def _run_batch(self, user_id: str, batch: list[str], round_id: int) -> None:
        state = self._state_for(user_id)
        with state.lock:
            state.mode = "RESPONDING"
            abort_requested = state.abort_requested
            nickname_for_batch = state.last_nickname

        def _fallback_timeout_result() -> ChatResult:
            return ChatResult(
                session_id=f"timeout-{user_id}-{round_id}",
                reply="（本轮思考超时，已中断当前回答。请继续发送，我会基于新一轮继续处理。）",
                session_emotion=0.0,
                global_emotion=0.0,
            )

        try:
            if settings.rhythm.enable_max_think_seconds:
                holder: dict[str, ChatResult | Exception | None] = {"result": None}
                finished = threading.Event()

                def _execute() -> None:
                    try:
                        holder["result"] = self.batch_executor(user_id, batch, abort_requested, nickname_for_batch)
                    except Exception as exc:  # pragma: no cover
                        holder["result"] = exc
                    finally:
                        finished.set()

                threading.Thread(
                    target=_execute,
                    name=f"window-batch-exec-{user_id}-{round_id}",
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
            else:
                result = self.batch_executor(user_id, batch, abort_requested, nickname_for_batch)
        except Exception as exc:
            with state.lock:
                for waiter in state.waiters:
                    if waiter.target_round == round_id:
                        waiter.holder["result"] = exc
                        waiter.event.set()
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
