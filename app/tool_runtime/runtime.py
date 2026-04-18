from __future__ import annotations

import logging
from dataclasses import asdict
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.services.llm_client import LLMClient
from app.tool_runtime.executor import execute_tool_call
from app.tool_runtime.registry import ToolRegistry
from app.tool_runtime.types import ToolCall, ToolExecutionContext, ToolResult

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
        for round_index in range(max(1, request.max_rounds)):
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
            turn_results = self._execute_turn_calls(
                request=request,
                round_index=round_index,
                calls=decision.tool_calls,
            )
            response.used_tool_calls.extend(decision.tool_calls)
            response.tool_results.extend(turn_results)
        logger.info("Tool runtime reached max rounds | scope=%s", request.scope_id)
        return response

    def _execute_turn_calls(
        self,
        *,
        request: ToolRuntimeRequest,
        round_index: int,
        calls: list[ToolCall],
    ) -> list[ToolResult]:
        indexed_results: dict[int, ToolResult] = {}
        concurrent_chunk: list[tuple[int, ToolCall]] = []
        for index, call in enumerate(calls):
            tool = self.registry.get(call.tool_name)
            if tool is None:
                indexed_results[index] = ToolResult(
                    tool_name=call.tool_name,
                    call_id=call.call_id,
                    ok=False,
                    error="tool not found",
                    error_code="tool_not_found",
                    error_details={"stage": "lookup"},
                )
                continue
            if tool.spec().concurrency_safe:
                concurrent_chunk.append((index, call))
                continue
            if concurrent_chunk:
                indexed_results.update(
                    self._execute_concurrent_chunk(
                        request=request,
                        round_index=round_index,
                        calls=concurrent_chunk,
                    )
                )
                concurrent_chunk = []
            indexed_results[index] = self._execute_single_call(
                request=request,
                round_index=round_index,
                call=call,
            )
        if concurrent_chunk:
            indexed_results.update(
                self._execute_concurrent_chunk(
                    request=request,
                    round_index=round_index,
                    calls=concurrent_chunk,
                )
            )

        final_results: list[ToolResult] = []
        for index, call in enumerate(calls):
            result = indexed_results.get(index)
            if result is None:
                result = ToolResult(
                    tool_name=call.tool_name,
                    call_id=call.call_id,
                    ok=False,
                    error="missing tool_result for tool_call",
                    error_code="internal_error",
                    error_details={"stage": "invariant_guard"},
                )
            if not result.call_id:
                result.call_id = call.call_id
            final_results.append(result)
        return final_results

    def _execute_concurrent_chunk(
        self,
        *,
        request: ToolRuntimeRequest,
        round_index: int,
        calls: list[tuple[int, ToolCall]],
    ) -> dict[int, ToolResult]:
        results: dict[int, ToolResult] = {}
        max_workers = max(1, min(4, len(calls)))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(
                    self._execute_single_call,
                    request=request,
                    round_index=round_index,
                    call=call,
                ): (idx, call)
                for idx, call in calls
            }
            for future, pair in futures.items():
                idx, call = pair
                try:
                    results[idx] = future.result()
                except Exception as exc:  # pragma: no cover
                    results[idx] = ToolResult(
                        tool_name=call.tool_name,
                        call_id=call.call_id,
                        ok=False,
                        error=str(exc),
                        error_code="internal_error",
                        error_details={"stage": "concurrent_execute"},
                    )
        return results

    def _execute_single_call(
        self,
        *,
        request: ToolRuntimeRequest,
        round_index: int,
        call: ToolCall,
    ) -> ToolResult:
        tool = self.registry.get(call.tool_name)
        if tool is None:
            return ToolResult(
                tool_name=call.tool_name,
                call_id=call.call_id,
                ok=False,
                error="tool not found",
                error_code="tool_not_found",
                error_details={"stage": "lookup"},
            )
        context = ToolExecutionContext(
            scope_id=request.scope_id,
            user_message=request.user_message,
            round_index=round_index,
        )
        return execute_tool_call(tool=tool, call=call, context=context)

    @staticmethod
    def _tool_result_to_dict(result: ToolResult) -> dict[str, Any]:
        return {
            "tool_name": result.tool_name,
            "call_id": result.call_id,
            "ok": result.ok,
            "data": result.data,
            "error": result.error,
            "error_code": result.error_code,
            "error_details": result.error_details,
            "meta": result.meta,
        }
