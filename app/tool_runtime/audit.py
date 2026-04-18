from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SendRateLimiter:
    limit_per_minute: int
    _history: Deque[float] | None = None

    def __post_init__(self) -> None:
        self._history = deque()

    def allow(self) -> bool:
        if self._history is None:
            self._history = deque()
        now = time.monotonic()
        cutoff = now - 60.0
        while self._history and self._history[0] < cutoff:
            self._history.popleft()
        if len(self._history) >= self.limit_per_minute:
            return False
        self._history.append(now)
        return True


def log_tool_audit(
    *,
    scope_id: str,
    tool_name: str,
    ok: bool,
    duration_ms: int,
    input_summary: str,
    error: str = "",
) -> None:
    logger.info(
        "Tool audit | scope=%s | tool=%s | ok=%s | duration_ms=%s | input=%s | error=%s",
        scope_id,
        tool_name,
        ok,
        duration_ms,
        input_summary,
        error.strip(),
    )
