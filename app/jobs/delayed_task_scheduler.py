from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
import logging
import threading
import time

from app.core.config import settings
from app.domain.models import DelayedTask
from app.jobs.task_queue import TaskQueue
from app.repos.interfaces import DelayedTaskRepo

logger = logging.getLogger(__name__)


class DelayedTaskScheduler:
    def __init__(
        self,
        *,
        repo: DelayedTaskRepo,
        task_queue: TaskQueue,
        executor: Callable[[DelayedTask], None],
    ) -> None:
        self.repo = repo
        self.task_queue = task_queue
        self.executor = executor
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._worker_id = f"worker-{id(self)}"

    def start(self) -> None:
        if not settings.delayed_task.enabled or self._thread is not None:
            return
        stale_before = datetime.now(timezone.utc) - timedelta(seconds=settings.delayed_task.stale_lease_seconds)
        recovered = self.repo.requeue_stale_running(stale_before_iso=stale_before.isoformat())
        if recovered > 0:
            logger.info("Delayed task stale recovery | count=%s", recovered)
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="delayed-task-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread is None:
            return
        self._stop_event.set()
        self._thread.join(timeout=2.0)
        self._thread = None

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            now_iso = datetime.now(timezone.utc).isoformat()
            due = self.repo.claim_due(
                now_iso=now_iso,
                limit=settings.delayed_task.claim_batch_size,
                worker_id=self._worker_id,
            )
            for task in due:
                self.task_queue.submit(self._execute_task, task)
            time.sleep(settings.delayed_task.poll_interval_seconds)

    def _execute_task(self, task: DelayedTask) -> None:
        try:
            self.executor(task)
            self.repo.mark_done(task.task_id)
            logger.info("Delayed task completed | task_id=%s", task.task_id)
        except Exception as exc:
            attempt = task.attempt_count + 1
            max_attempts = max(1, task.max_attempts)
            if attempt >= max_attempts:
                self.repo.mark_dead(task_id=task.task_id, last_error=str(exc))
                logger.exception("Delayed task dead | task_id=%s | err=%s", task.task_id, exc)
                return
            backoffs = settings.delayed_task.retry_backoff_seconds or [10.0, 30.0, 60.0]
            delay = backoffs[min(attempt - 1, len(backoffs) - 1)]
            next_run = datetime.now(timezone.utc) + timedelta(seconds=max(1.0, float(delay)))
            self.repo.mark_retry(
                task_id=task.task_id,
                next_run_at_iso=next_run.isoformat(),
                last_error=str(exc),
            )
            logger.warning(
                "Delayed task retry scheduled | task_id=%s | attempt=%s | delay=%.1fs | err=%s",
                task.task_id,
                attempt,
                delay,
                exc,
            )
