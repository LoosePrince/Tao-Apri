from dataclasses import dataclass
import time

from app.core.config import settings
from app.services.channel_sender import ChannelRouter, SendMessageRequest
from app.tool_runtime.audit import SendRateLimiter
from app.tool_runtime.builtin_tools import QueryMessagesTool, SendMessageTool
from app.tool_runtime.registry import ToolRegistry
from app.tool_runtime.digest import build_execution_digest
from app.tool_runtime.runtime import ToolRuntime, ToolRuntimeRequest, ToolRuntimeResponse
from app.tool_runtime.types import ToolCall, ToolLoopDecision, ToolResult, ToolSpec


class _FakeMessage:
    def __init__(self, idx: int, user_id: str = "u1") -> None:
        from datetime import datetime, timezone

        self.message_id = f"m{idx}"
        self.user_id = user_id
        self.role = "user" if idx % 2 else "assistant"
        self.session_id = "s1"
        self.scope_id = "private:u1"
        self.sanitized_content = f"text-{idx}"
        self.created_at = datetime.now(timezone.utc)
        self.source_message_id = f"src-{idx}"


class _MessageRepoStub:
    def list_all(self, limit: int = 200):
        return [_FakeMessage(i) for i in range(1, min(limit, 5) + 1)]


@dataclass
class _EchoTool:
    def spec(self) -> ToolSpec:
        return ToolSpec(name="echo_tool", description="echo", input_schema={"type": "object"})

    def call(self, payload: dict):
        return ToolResult(tool_name="echo_tool", call_id="", ok=True, data={"echo": payload.get("value", "")})


@dataclass
class _SlowConcurrentTool:
    name: str
    delay_seconds: float = 0.25
    read_only: bool = True
    concurrency_safe: bool = True

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description="slow concurrent",
            input_schema={"type": "object", "properties": {"value": {"type": "string"}}},
            read_only=self.read_only,
            concurrency_safe=self.concurrency_safe,
        )

    def call(self, payload: dict):
        time.sleep(self.delay_seconds)
        return ToolResult(tool_name=self.name, call_id="", ok=True, data={"echo": payload.get("value", "")})


class _LLMStub:
    def __init__(self) -> None:
        self._step = 0

    def plan_tool_loop_step(self, *, user_message: str, tool_specs: list[dict], tool_results: list[dict]) -> ToolLoopDecision:
        del user_message, tool_specs
        self._step += 1
        if self._step == 1:
            return ToolLoopDecision(tool_calls=[ToolCall(tool_name="echo_tool", input={"value": "ok"}, call_id="c1")])
        assert tool_results and tool_results[0]["data"]["echo"] == "ok"
        return ToolLoopDecision(tool_calls=[])


class _SenderStub:
    def __init__(self) -> None:
        self.calls: list[SendMessageRequest] = []

    def send(self, request: SendMessageRequest) -> str:
        self.calls.append(request)
        return "platform-id-1"


class _MissingToolLLMStub:
    def plan_tool_loop_step(self, *, user_message: str, tool_specs: list[dict], tool_results: list[dict]) -> ToolLoopDecision:
        del user_message, tool_specs, tool_results
        return ToolLoopDecision(tool_calls=[ToolCall(tool_name="unknown_tool", input={}, call_id="missing-1")])


@dataclass
class _FlakyTool:
    name: str = "flaky_tool"

    def __post_init__(self) -> None:
        self.calls = 0

    def spec(self) -> ToolSpec:
        return ToolSpec(name=self.name, description="flaky", input_schema={"type": "object"}, concurrency_safe=False)

    def call(self, payload: dict):
        del payload
        self.calls += 1
        if self.calls == 1:
            raise TimeoutError("temporary timeout")
        return ToolResult(tool_name=self.name, call_id="", ok=True, data={"ok": True})


@dataclass
class _LargeResultTool:
    name: str = "large_result_tool"

    def spec(self) -> ToolSpec:
        return ToolSpec(name=self.name, description="large", input_schema={"type": "object"})

    def call(self, payload: dict):
        del payload
        return ToolResult(tool_name=self.name, call_id="", ok=True, data={"blob": "x" * 5000})


def test_tool_runtime_loop_executes_until_handoff():
    registry = ToolRegistry()
    registry.register(_EchoTool())
    runtime = ToolRuntime(llm_client=_LLMStub(), registry=registry)
    response = runtime.run(ToolRuntimeRequest(scope_id="private:u1", user_message="hi", max_rounds=3))
    assert response.final_reply == ""
    assert len(response.used_tool_calls) == 1
    assert response.tool_results[0].ok is True


def test_query_messages_tool_filters_and_limits():
    tool = QueryMessagesTool(message_repo=_MessageRepoStub())  # type: ignore[arg-type]
    result = tool.call({"role": "user", "limit": 2})
    assert result.ok is True
    messages = result.data["messages"]
    assert len(messages) <= 2
    assert all(item["role"] == "user" for item in messages)


def test_send_message_tool_respects_force_whitelist():
    original_force = settings.tools.force_send_whitelist
    original_whitelist = list(settings.tools.allowed_send_targets)
    try:
        settings.tools.force_send_whitelist = True
        settings.tools.allowed_send_targets = ["qq:group:123"]
        router = ChannelRouter()
        sender = _SenderStub()
        router.register("qq", sender)
        tool = SendMessageTool(router=router, rate_limiter=SendRateLimiter(limit_per_minute=5))

        denied = tool.call({"channel": "qq", "target_type": "group", "target_id": "321", "content": "hello"})
        assert denied.ok is False

        allowed = tool.call({"channel": "qq", "target_type": "group", "target_id": "123", "content": "hello"})
        assert allowed.ok is True
        assert sender.calls
    finally:
        settings.tools.force_send_whitelist = original_force
        settings.tools.allowed_send_targets = original_whitelist


def test_tool_runtime_concurrent_batch_keeps_order():
    registry = ToolRegistry()
    registry.register(_SlowConcurrentTool(name="tool_a"))
    registry.register(_SlowConcurrentTool(name="tool_b"))

    class _ConcurrentLLMStub:
        def __init__(self) -> None:
            self._step = 0

        def plan_tool_loop_step(self, *, user_message: str, tool_specs: list[dict], tool_results: list[dict]) -> ToolLoopDecision:
            del user_message, tool_specs
            self._step += 1
            if self._step == 1:
                return ToolLoopDecision(
                    tool_calls=[
                        ToolCall(tool_name="tool_a", input={"value": "A"}, call_id="a-1"),
                        ToolCall(tool_name="tool_b", input={"value": "B"}, call_id="b-1"),
                    ]
                )
            assert [item["call_id"] for item in tool_results] == ["a-1", "b-1"]
            return ToolLoopDecision(tool_calls=[])

    runtime = ToolRuntime(llm_client=_ConcurrentLLMStub(), registry=registry)
    started = time.perf_counter()
    response = runtime.run(ToolRuntimeRequest(scope_id="private:u1", user_message="hi", max_rounds=3))
    elapsed = time.perf_counter() - started
    assert response.final_reply == ""
    assert [item.call_id for item in response.tool_results] == ["a-1", "b-1"]
    assert elapsed < 0.45


def test_tool_runtime_permission_gate_denies_non_readonly_when_configured():
    original = settings.tools.non_readonly_permission_behavior
    try:
        settings.tools.non_readonly_permission_behavior = "deny"
        registry = ToolRegistry()
        registry.register(_SlowConcurrentTool(name="write_tool", read_only=False, concurrency_safe=False))

        class _OneTurnLLMStub:
            def __init__(self) -> None:
                self._step = 0

            def plan_tool_loop_step(self, *, user_message: str, tool_specs: list[dict], tool_results: list[dict]) -> ToolLoopDecision:
                del user_message, tool_specs
                self._step += 1
                if self._step == 1:
                    return ToolLoopDecision(tool_calls=[ToolCall(tool_name="write_tool", input={"value": "x"}, call_id="w-1")])
                assert tool_results
                return ToolLoopDecision(tool_calls=[])

        runtime = ToolRuntime(llm_client=_OneTurnLLMStub(), registry=registry)
        response = runtime.run(ToolRuntimeRequest(scope_id="private:u1", user_message="hi", max_rounds=2))
        result = response.tool_results[0]
        assert result.ok is False
        assert result.error_code == "permission_denied"
    finally:
        settings.tools.non_readonly_permission_behavior = original


def test_tool_runtime_invariant_guard_fills_missing_result_for_unknown_tool():
    registry = ToolRegistry()
    runtime = ToolRuntime(llm_client=_MissingToolLLMStub(), registry=registry)
    response = runtime.run(ToolRuntimeRequest(scope_id="private:u1", user_message="hi", max_rounds=1))
    assert len(response.tool_results) == 1
    assert response.tool_results[0].call_id == "missing-1"
    assert response.tool_results[0].error_code == "tool_not_found"


def test_tool_runtime_retries_retryable_error_then_succeeds():
    original_attempts = settings.tools.retry_max_attempts
    try:
        settings.tools.retry_max_attempts = 2
        registry = ToolRegistry()
        flaky = _FlakyTool()
        registry.register(flaky)

        class _OneCallLLM:
            def __init__(self) -> None:
                self._step = 0

            def plan_tool_loop_step(self, *, user_message: str, tool_specs: list[dict], tool_results: list[dict]) -> ToolLoopDecision:
                del user_message, tool_specs
                self._step += 1
                if self._step == 1:
                    return ToolLoopDecision(tool_calls=[ToolCall(tool_name="flaky_tool", input={}, call_id="f1")])
                assert tool_results and tool_results[0]["ok"] is True
                return ToolLoopDecision(tool_calls=[])

        runtime = ToolRuntime(llm_client=_OneCallLLM(), registry=registry)
        response = runtime.run(ToolRuntimeRequest(scope_id="private:u1", user_message="hi", max_rounds=2))
        assert response.final_reply == ""
        assert response.tool_results[0].ok is True
    finally:
        settings.tools.retry_max_attempts = original_attempts


def test_tool_runtime_result_budget_truncates_large_payload():
    original_per = settings.tools.result_budget_per_tool_chars
    original_total = settings.tools.result_budget_total_chars
    try:
        settings.tools.result_budget_per_tool_chars = 200
        settings.tools.result_budget_total_chars = 400
        registry = ToolRegistry()
        registry.register(_LargeResultTool())

        class _SingleRoundLLM:
            def __init__(self) -> None:
                self._step = 0

            def plan_tool_loop_step(self, *, user_message: str, tool_specs: list[dict], tool_results: list[dict]) -> ToolLoopDecision:
                del user_message, tool_specs, tool_results
                self._step += 1
                if self._step == 1:
                    return ToolLoopDecision(tool_calls=[ToolCall(tool_name="large_result_tool", input={}, call_id="l1")])
                return ToolLoopDecision(tool_calls=[])

        runtime = ToolRuntime(llm_client=_SingleRoundLLM(), registry=registry)
        response = runtime.run(ToolRuntimeRequest(scope_id="private:u1", user_message="hi", max_rounds=1))
        assert response.tool_results
        item = response.tool_results[0]
        assert item.meta.get("truncated") is True
        assert item.meta.get("ref_id")
        assert item.meta.get("original_size", 0) > 200
    finally:
        settings.tools.result_budget_per_tool_chars = original_per
        settings.tools.result_budget_total_chars = original_total


def test_build_execution_digest_includes_tool_trace():
    response = ToolRuntimeResponse()
    response.used_tool_calls = [ToolCall(tool_name="echo_tool", input={"value": "x"}, call_id="c1")]
    response.tool_results = [
        ToolResult(tool_name="echo_tool", call_id="c1", ok=True, data={"echo": "x"}),
    ]
    digest = build_execution_digest(response, max_chars=4000)
    assert "echo_tool" in digest
    assert "echo" in digest
    assert "ok" in digest
