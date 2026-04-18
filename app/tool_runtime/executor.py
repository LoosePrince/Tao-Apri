from __future__ import annotations

import json
import time
from typing import Any

from app.tool_runtime.audit import log_tool_audit
from app.tool_runtime.permissions import decide_tool_permission
from app.tool_runtime.types import Tool, ToolCall, ToolExecutionContext, ToolResult


def execute_tool_call(
    *,
    tool: Tool,
    call: ToolCall,
    context: ToolExecutionContext,
) -> ToolResult:
    tool_name = call.tool_name
    input_summary = json.dumps(call.input, ensure_ascii=False)[:200]
    start = time.perf_counter()
    spec = tool.spec()

    schema_ok, schema_message = _validate_schema(spec.input_schema, call.input)
    if not schema_ok:
        return _finalize(
            context=context,
            tool_name=tool_name,
            call_id=call.call_id,
            ok=False,
            input_summary=input_summary,
            duration_ms=_elapsed_ms(start),
            error=schema_message or "invalid input by schema",
            error_code="invalid_input",
            error_details={"stage": "schema_validate"},
        )

    validate_fn = getattr(tool, "validate_input", None)
    if callable(validate_fn):
        try:
            valid, message = validate_fn(call.input)
        except Exception as exc:  # pragma: no cover
            return _finalize(
                context=context,
                tool_name=tool_name,
                call_id=call.call_id,
                ok=False,
                input_summary=input_summary,
                duration_ms=_elapsed_ms(start),
                error=str(exc),
                error_code="invalid_input",
                error_details={"stage": "business_validate"},
            )
        if not valid:
            return _finalize(
                context=context,
                tool_name=tool_name,
                call_id=call.call_id,
                ok=False,
                input_summary=input_summary,
                duration_ms=_elapsed_ms(start),
                error=message or "invalid input",
                error_code="invalid_input",
                error_details={"stage": "business_validate"},
            )

    permission = decide_tool_permission(tool_spec=spec, context=context)
    if permission.behavior != "allow":
        return _finalize(
            context=context,
            tool_name=tool_name,
            call_id=call.call_id,
            ok=False,
            input_summary=input_summary,
            duration_ms=_elapsed_ms(start),
            error=f"permission {permission.behavior}: {permission.reason}",
            error_code="permission_denied",
            error_details={"behavior": permission.behavior, "reason": permission.reason, "source": permission.source},
        )

    try:
        raw_result = tool.call(call.input)
    except TimeoutError as exc:
        return _finalize(
            context=context,
            tool_name=tool_name,
            call_id=call.call_id,
            ok=False,
            input_summary=input_summary,
            duration_ms=_elapsed_ms(start),
            error=str(exc),
            error_code="timeout",
            error_details={"stage": "execute"},
        )
    except Exception as exc:  # pragma: no cover
        return _finalize(
            context=context,
            tool_name=tool_name,
            call_id=call.call_id,
            ok=False,
            input_summary=input_summary,
            duration_ms=_elapsed_ms(start),
            error=str(exc),
            error_code="execution_failed",
            error_details={"stage": "execute"},
        )

    raw_result.call_id = call.call_id
    if not raw_result.ok and not raw_result.error_code:
        raw_result.error_code = "execution_failed"
    return _finalize_result(context=context, input_summary=input_summary, started=start, result=raw_result)


def _validate_schema(schema: dict[str, Any], payload: dict[str, Any]) -> tuple[bool, str]:
    schema_type = str(schema.get("type", "")).strip()
    if schema_type and schema_type != "object":
        return False, "schema type must be object"
    if not isinstance(payload, dict):
        return False, "payload must be object"
    required = schema.get("required", [])
    if isinstance(required, list):
        for key in required:
            key_text = str(key).strip()
            if key_text and key_text not in payload:
                return False, f"missing required field: {key_text}"
    properties = schema.get("properties", {})
    if isinstance(properties, dict):
        for key, rule in properties.items():
            if key not in payload or not isinstance(rule, dict):
                continue
            expected = str(rule.get("type", "")).strip()
            if not expected:
                continue
            value = payload[key]
            if expected == "string" and not isinstance(value, str):
                return False, f"{key} must be string"
            if expected == "integer" and not (isinstance(value, int) and not isinstance(value, bool)):
                return False, f"{key} must be integer"
            if expected == "number" and not isinstance(value, (int, float)):
                return False, f"{key} must be number"
            if expected == "object" and not isinstance(value, dict):
                return False, f"{key} must be object"
    return True, ""


def _finalize_result(*, context: ToolExecutionContext, input_summary: str, started: float, result: ToolResult) -> ToolResult:
    duration_ms = _elapsed_ms(started)
    log_tool_audit(
        scope_id=context.scope_id,
        tool_name=result.tool_name,
        ok=result.ok,
        duration_ms=duration_ms,
        input_summary=input_summary,
        error=result.error,
    )
    return result


def _finalize(
    *,
    context: ToolExecutionContext,
    tool_name: str,
    call_id: str,
    ok: bool,
    input_summary: str,
    duration_ms: int,
    error: str,
    error_code: str,
    error_details: dict[str, Any],
) -> ToolResult:
    result = ToolResult(
        tool_name=tool_name,
        call_id=call_id,
        ok=ok,
        data={},
        error=error,
        error_code=error_code,
        error_details=error_details,
    )
    log_tool_audit(
        scope_id=context.scope_id,
        tool_name=tool_name,
        ok=ok,
        duration_ms=duration_ms,
        input_summary=input_summary,
        error=error,
    )
    return result


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
