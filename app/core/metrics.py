from __future__ import annotations

from collections import deque
import threading
import time


class MetricsRegistry:
    def __init__(self, *, latency_window_size: int = 500) -> None:
        self._lock = threading.Lock()
        self._started_at = time.monotonic()
        self._requests = 0
        self._errors = 0
        self._latencies_ms: deque[float] = deque(maxlen=latency_window_size)
        self._counters: dict[str, int] = {}

    def observe_request(self, *, latency_ms: float, is_error: bool) -> None:
        with self._lock:
            self._requests += 1
            if is_error:
                self._errors += 1
            self._latencies_ms.append(max(0.0, latency_ms))

    def inc(self, key: str, delta: int = 1) -> None:
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + delta

    def snapshot(self) -> dict[str, float | int]:
        with self._lock:
            elapsed = max(1e-6, time.monotonic() - self._started_at)
            qps = self._requests / elapsed
            error_rate = self._errors / max(1, self._requests)
            p50, p95 = self._percentiles(list(self._latencies_ms))
            payload: dict[str, float | int] = {
                "requests": self._requests,
                "errors": self._errors,
                "qps": qps,
                "error_rate": error_rate,
                "latency_p50_ms": p50,
                "latency_p95_ms": p95,
            }
            payload.update(self._counters)
            return payload

    @staticmethod
    def _percentiles(values: list[float]) -> tuple[float, float]:
        if not values:
            return 0.0, 0.0
        ordered = sorted(values)
        p50_idx = int(0.5 * (len(ordered) - 1))
        p95_idx = int(0.95 * (len(ordered) - 1))
        return ordered[p50_idx], ordered[p95_idx]
