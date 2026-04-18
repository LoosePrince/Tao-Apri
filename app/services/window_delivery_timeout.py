"""Marks window rounds where the user-facing path timed out before delivery.

When the batch executor still persists an assistant reply later, the stored content
is prefixed so retrieval/history reflects an unsent-on-time reply.
"""

from __future__ import annotations

import threading

LATE_ASSISTANT_DELIVERY_PREFIX = "[此消息超时未成功发送]"

_lock = threading.Lock()
_marked_rounds: set[tuple[str, int]] = set()


def mark_late_assistant_delivery(scope_id: str, window_round_id: int) -> None:
    key = (scope_id, int(window_round_id))
    with _lock:
        _marked_rounds.add(key)


def consume_late_assistant_delivery(scope_id: str, window_round_id: int | None) -> bool:
    if window_round_id is None:
        return False
    key = (scope_id, int(window_round_id))
    with _lock:
        if key in _marked_rounds:
            _marked_rounds.discard(key)
            return True
        return False
