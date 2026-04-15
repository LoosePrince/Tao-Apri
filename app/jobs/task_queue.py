from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from queue import Full, Queue
import logging
import threading

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class DeadLetter:
    fn: Callable[..., None]
    args: tuple
    kwargs: dict
    job_name: str
    attempts: int
    error: str
    args_repr: str
    kwargs_repr: str


class TaskQueue:
    def __init__(
        self,
        *,
        enabled: bool,
        worker_count: int,
        queue_size: int,
        max_retries: int = 2,
        dead_letter_limit: int = 200,
    ) -> None:
        self.enabled = enabled
        self._queue: Queue[tuple[Callable[..., None] | None, tuple, dict, int]] = Queue(maxsize=queue_size)
        self._worker_count = worker_count
        self._threads: list[threading.Thread] = []
        self._running = False
        self._max_retries = max_retries
        self._dead_letter_limit = dead_letter_limit
        self._dead_letters: list[DeadLetter] = []

    def start(self) -> None:
        if not self.enabled or self._running:
            return
        self._running = True
        for idx in range(self._worker_count):
            thread = threading.Thread(target=self._worker_loop, name=f"task-queue-{idx}", daemon=True)
            thread.start()
            self._threads.append(thread)
        logger.info("Task queue started | workers=%s", self._worker_count)

    def stop(self) -> None:
        if not self.enabled or not self._running:
            return
        self._running = False
        for _ in self._threads:
            self._queue.put((None, (), {}, 0))
        for thread in self._threads:
            thread.join(timeout=2.0)
        self._threads.clear()
        logger.info("Task queue stopped")

    def submit(self, fn: Callable[..., None], *args, **kwargs) -> None:  # noqa: ANN002,ANN003
        if not self.enabled:
            fn(*args, **kwargs)
            return
        if not self._running:
            fn(*args, **kwargs)
            return
        try:
            self._queue.put_nowait((fn, args, kwargs, 0))
        except Full:
            logger.warning("Task queue full, execute task inline")
            fn(*args, **kwargs)

    def list_dead_letters(self) -> list[DeadLetter]:
        return list(self._dead_letters)

    def replay_dead_letters(self, limit: int = 20) -> int:
        if not self.enabled or not self._running:
            return 0
        replayed = 0
        remaining: list[DeadLetter] = []
        for item in self._dead_letters:
            if replayed < limit:
                replayed += 1
                logger.info("Replaying dead letter job | job=%s | attempts=%s", item.job_name, item.attempts)
                try:
                    self._queue.put_nowait((item.fn, item.args, item.kwargs, 0))
                except Full:
                    remaining.append(item)
            else:
                remaining.append(item)
        self._dead_letters = remaining
        return replayed

    def _worker_loop(self) -> None:
        while True:
            fn, args, kwargs, attempts = self._queue.get()
            if fn is None:
                self._queue.task_done()
                return
            try:
                fn(*args, **kwargs)
            except Exception as exc:
                if attempts < self._max_retries:
                    logger.warning(
                        "Task queue job failed, retrying | job=%s | attempt=%s/%s | err=%s",
                        getattr(fn, "__name__", "unknown"),
                        attempts + 1,
                        self._max_retries,
                        exc,
                    )
                    try:
                        self._queue.put_nowait((fn, args, kwargs, attempts + 1))
                    except Full:
                        self._push_dead_letter(fn=fn, args=args, kwargs=kwargs, attempts=attempts + 1, error=exc)
                else:
                    logger.exception("Task queue job failed and moved to dead letter: %s", exc)
                    self._push_dead_letter(fn=fn, args=args, kwargs=kwargs, attempts=attempts + 1, error=exc)
            finally:
                self._queue.task_done()

    def _push_dead_letter(
        self,
        *,
        fn: Callable[..., None],
        args: tuple,
        kwargs: dict,
        attempts: int,
        error: Exception,
    ) -> None:
        self._dead_letters.append(
            DeadLetter(
                fn=fn,
                args=args,
                kwargs=kwargs,
                job_name=getattr(fn, "__name__", "unknown"),
                attempts=attempts,
                error=str(error),
                args_repr=repr(args),
                kwargs_repr=repr(kwargs),
            )
        )
        if len(self._dead_letters) > self._dead_letter_limit:
            self._dead_letters = self._dead_letters[-self._dead_letter_limit :]
