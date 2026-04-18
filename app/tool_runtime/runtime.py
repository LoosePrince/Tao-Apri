from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict
from dataclasses import dataclass, field
from typing import Any

from app.services.llm_client import LLMClient
from app.tool_runtime.audit import log_tool_audit
from app.tool_runtime.registry import ToolRegistry
from app.tool_runtime.types import ToolCall, ToolLoopDecision, ToolResult

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ToolRuntimeRequest:
    scope_id: str
    user_message: str
    max_rounds: int


@dataclass(slots=True)
class ToolRuntimeResponse:
    final_reply: str = ""
    used_tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)


class ToolRuntime:
    def __init__(self, *, llm_client: LLMClient, registry: ToolRegistry) -> None:
        self.llm_client = llm_client
        self.registry = registry

    def run(self, request: ToolRuntimeRequest) -> ToolRuntimeResponse:
        response = ToolRuntimeResponse()
        for _ in range(max(1, request.max_rounds)):
            decision = self.llm_client.plan_tool_loop_step(
                user_message=request.user_message,
                tool_specs=[asdict(spec) for spec in self.registry.specs()],
                tool_results=[self._tool_result_to_dict(item) for item in response.tool_results],
            )
            if decision.final_reply.strip():
                response.final_reply = decision.final_reply.strip()
                return response
            if not decision.tool_calls:
                return response
            for call in decision.tool_calls:
                tool = self.registry.get(call.tool_name)
                if tool is None:
                    result = ToolResult(
                        tool_name=call.tool_name,
                        call_id=call.call_id,
                        ok=False,
                        error="tool not found",
                    )
                    response.tool_results.append(result)
                    continue
                start = time.perf_counter()
                raw_result = tool.call(call.input)
                raw_result.call_id = call.call_id
                duration_ms = int((time.perf_counter() - start) * 1000)
                log_tool_audit(
                    scope_id=request.scope_id,
                    tool_name=call.tool_name,
                    ok=raw_result.ok,
                    duration_ms=duration_ms,
                    input_summary=json.dumps(call.input, ensure_ascii=False)[:200],
                    error=raw_result.error,
                )
                response.used_tool_calls.append(call)
                response.tool_results.append(raw_result)
        logger.info("Tool runtime reached max rounds | scope=%s", request.scope_id)
        return response

    @staticmethod
    def _tool_result_to_dict(result: ToolResult) -> dict[str, Any]:
        return {
            "tool_name": result.tool_name,
            "call_id": result.call_id,
            "ok": result.ok,
            "data": result.data,
            "error": result.error,
            "meta": result.meta,
        }
