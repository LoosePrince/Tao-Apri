from __future__ import annotations

from collections.abc import Callable
from queue import Full, Queue
import logging
import threading

logger = logging.getLogger(__name__)


class TaskQueue:
    def __init__(self, *, enabled: bool, worker_count: int, queue_size: int) -> None:
        self.enabled = enabled
        self._queue: Queue[tuple[Callable[..., None] | None, tuple, dict]] = Queue(maxsize=queue_size)
        self._worker_count = worker_count
        self._threads: list[threading.Thread] = []
        self._running = False

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
            self._queue.put((None, (), {}))
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
            self._queue.put_nowait((fn, args, kwargs))
        except Full:
            logger.warning("Task queue full, execute task inline")
            fn(*args, **kwargs)

    def _worker_loop(self) -> None:
        while True:
            fn, args, kwargs = self._queue.get()
            if fn is None:
                self._queue.task_done()
                return
            try:
                fn(*args, **kwargs)
            except Exception as exc:  # pragma: no cover
                logger.exception("Task queue job failed: %s", exc)
            finally:
                self._queue.task_done()
