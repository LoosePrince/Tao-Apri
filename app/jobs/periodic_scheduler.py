from __future__ import annotations

from collections.abc import Callable
import logging
import threading
import time

logger = logging.getLogger(__name__)


class PeriodicScheduler:
    def __init__(self, *, enabled: bool) -> None:
        self.enabled = enabled
        self._jobs: list[tuple[str, float, Callable[[], None]]] = []
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def add_job(self, *, name: str, interval_seconds: float, job: Callable[[], None]) -> None:
        self._jobs.append((name, interval_seconds, job))

    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="periodic-scheduler", daemon=True)
        self._thread.start()
        logger.info("Periodic scheduler started | jobs=%s", len(self._jobs))

    def stop(self) -> None:
        if not self.enabled or self._thread is None:
            return
        self._stop_event.set()
        self._thread.join(timeout=2.0)
        self._thread = None
        logger.info("Periodic scheduler stopped")

    def run_once(self) -> None:
        for name, _, job in self._jobs:
            try:
                job()
            except Exception as exc:  # pragma: no cover
                logger.exception("Periodic job failed | job=%s | err=%s", name, exc)

    def _run_loop(self) -> None:
        if not self._jobs:
            return
        next_run: dict[str, float] = {}
        now = time.monotonic()
        for name, interval, _ in self._jobs:
            next_run[name] = now + interval
        while not self._stop_event.is_set():
            now = time.monotonic()
            for name, interval, job in self._jobs:
                if now >= next_run[name]:
                    try:
                        job()
                    except Exception as exc:  # pragma: no cover
                        logger.exception("Periodic job failed | job=%s | err=%s", name, exc)
                    next_run[name] = now + interval
            time.sleep(0.2)
