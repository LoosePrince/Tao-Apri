from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


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
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    read_only: bool = True
    concurrency_safe: bool = False


ToolErrorCode = Literal[
    "invalid_input",
    "permission_denied",
    "execution_failed",
    "timeout",
    "tool_not_found",
    "internal_error",
]


PermissionBehavior = Literal["allow", "ask", "deny"]


@dataclass(slots=True)
class PermissionDecision:
    behavior: PermissionBehavior
    reason: str
    source: str = "tool_runtime_policy"


@dataclass(slots=True)
class ToolExecutionContext:
    scope_id: str
    user_message: str
    round_index: int


@dataclass(slots=True)
class ToolLoopDecision:
    """Tool planning step. User-visible copy is produced by unified synthesis, not `final_reply`."""

    final_reply: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = ""


class Tool(Protocol):
    def spec(self) -> ToolSpec: ...

    def validate_input(self, payload: dict[str, Any]) -> tuple[bool, str]: ...

    def call(self, payload: dict[str, Any]) -> ToolResult: ...
