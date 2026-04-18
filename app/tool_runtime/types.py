from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(slots=True)
class ToolCall:
    tool_name: str
    input: dict[str, Any]
    call_id: str


@dataclass(slots=True)
class ToolResult:
    tool_name: str
    call_id: str
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    read_only: bool = True
    concurrency_safe: bool = False


@dataclass(slots=True)
class ToolLoopDecision:
    final_reply: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = ""


class Tool(Protocol):
    def spec(self) -> ToolSpec: ...

    def call(self, payload: dict[str, Any]) -> ToolResult: ...
