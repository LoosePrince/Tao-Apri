from __future__ import annotations

from app.core.config import settings
from app.tool_runtime.types import PermissionDecision, ToolExecutionContext, ToolSpec


def decide_tool_permission(*, tool_spec: ToolSpec, context: ToolExecutionContext) -> PermissionDecision:
    del context
    if tool_spec.read_only:
        return PermissionDecision(behavior="allow", reason="read_only_tool")
    raw_behavior = str(getattr(settings.tools, "non_readonly_permission_behavior", "allow")).strip().lower()
    if raw_behavior not in {"allow", "ask", "deny"}:
        raw_behavior = "allow"
    if raw_behavior == "allow":
        return PermissionDecision(behavior="allow", reason="non_readonly_default_allow")
    if raw_behavior == "ask":
        return PermissionDecision(behavior="ask", reason="non_readonly_requires_confirmation")
    return PermissionDecision(behavior="deny", reason="non_readonly_denied_by_policy")
