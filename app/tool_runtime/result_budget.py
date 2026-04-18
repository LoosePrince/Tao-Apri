from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path
from uuid import uuid4

from app.tool_runtime.types import ToolResult

_CACHE_DIR = Path(tempfile.gettempdir()) / "taoapri_tool_result_refs"
logger = logging.getLogger(__name__)


def apply_result_budget(
    *,
    tool_results: list[ToolResult],
    per_result_max_chars: int,
    total_max_chars: int,
) -> tuple[list[ToolResult], int]:
    truncated_count = 0
    sized_items: list[tuple[ToolResult, str]] = []
    total = 0
    for item in tool_results:
        payload_text = json.dumps({"data": item.data, "error": item.error, "meta": item.meta}, ensure_ascii=False)
        sized_items.append((item, payload_text))
        total += len(payload_text)

    for item, payload_text in sized_items:
        if len(payload_text) > per_result_max_chars:
            _truncate_result(item, payload_text, per_result_max_chars)
            truncated_count += 1

    if total <= total_max_chars:
        return tool_results, truncated_count

    for item, payload_text in reversed(sized_items):
        if total <= total_max_chars:
            break
        before = len(payload_text)
        _truncate_result(item, payload_text, max(256, per_result_max_chars // 2))
        after = len(json.dumps({"data": item.data, "error": item.error, "meta": item.meta}, ensure_ascii=False))
        total -= max(0, before - after)
        truncated_count += 1
    return tool_results, truncated_count


def _truncate_result(result: ToolResult, payload_text: str, max_chars: int) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    ref_id = f"tr_{uuid4().hex[:10]}"
    ref_path = _CACHE_DIR / f"{ref_id}.json"
    ref_path.write_text(payload_text, encoding="utf-8")
    preview = payload_text[:max(0, max_chars)]
    result.data = {
        "summary": preview,
        "notice": "result truncated, use ref_id to inspect full payload",
    }
    result.meta["truncated"] = True
    result.meta["ref_id"] = ref_id
    result.meta["original_size"] = len(payload_text)
    logger.info(
        "Tool result budget applied | tool_name=%s | call_id=%s | ref_id=%s | original_size=%s | max_chars=%s",
        result.tool_name,
        result.call_id,
        ref_id,
        len(payload_text),
        max_chars,
    )
