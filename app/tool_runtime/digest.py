from __future__ import annotations

import json

from app.core.config import settings
from app.tool_runtime.runtime import ToolRuntimeResponse
from app.tool_runtime.types import ToolCall, ToolResult


def _shorten(text: str, max_len: int) -> str:
    text = text.strip()
    if len(text) <= max_len:
        return text
    return text[: max(0, max_len - 1)] + "…"


def build_execution_digest(response: ToolRuntimeResponse, *, max_chars: int | None = None) -> str:
    """Readable trace of tool inputs/outputs for injection into unified synthesis (budget-limited)."""
    limit = max_chars if max_chars is not None else settings.tools.unified_digest_max_chars
    calls: list[ToolCall] = response.used_tool_calls
    results: list[ToolResult] = response.tool_results
    lines: list[str] = []
    n = min(len(calls), len(results))
    for i in range(n):
        call = calls[i]
        res = results[i]
        inp_raw = json.dumps(call.input, ensure_ascii=False)
        inp = _shorten(inp_raw, 600)
        cid = call.call_id or f"call_{i+1}"
        if res.ok:
            data_raw = json.dumps(res.data, ensure_ascii=False)
            data = _shorten(data_raw, 1200)
            lines.append(f"[{i+1}] {call.tool_name} id={cid} ok | input={inp} | data={data}")
        else:
            err = _shorten(res.error or "", 400)
            lines.append(
                f"[{i+1}] {call.tool_name} id={cid} FAIL | input={inp} | error={err!r} | code={res.error_code}"
            )
    if len(calls) != len(results):
        lines.append(f"[digest: warning] calls={len(calls)} results={len(results)} count mismatch")

    text = "\n".join(lines) if lines else "（本回合未执行工具或无任何结果记录）"
    return _shorten(text, limit) if len(text) > limit else text
