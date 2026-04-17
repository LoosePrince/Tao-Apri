from __future__ import annotations

from types import SimpleNamespace

from app.core.config import settings
from app.services.llm_client import LLMClient
from app.services.prompt_composer import PromptContext


class _FakeCompletions:
    def __init__(self, payload: str) -> None:
        self._payload = payload

    def create(self, **kwargs):  # noqa: ANN003
        del kwargs
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self._payload))])


class _FakeClient:
    def __init__(self, payload: str) -> None:
        self.chat = SimpleNamespace(completions=_FakeCompletions(payload))


class _UnifiedLLMClient(LLMClient):
    def __init__(self, payload: str) -> None:
        super().__init__()
        self._payload = payload

    def _get_client(self):  # type: ignore[override]
        return _FakeClient(self._payload)


def _prompt_ctx() -> PromptContext:
    return PromptContext(
        system_core="core",
        system_runtime="runtime",
        memory_context="memory",
        policy_notice="policy",
        parameter_context="params",
        profile_context="profile",
        user_message="你好",
    )


def test_generate_unified_decision_forces_empty_reply_when_skip() -> None:
    old_provider = settings.llm.provider
    old_key = settings.llm.api_key
    settings.llm.provider = "kilo"
    settings.llm.api_key = "test-key"
    try:
        payload = (
            '{"should_reply":"false","skip_reason":"skip:test","reply":"should be removed",'
            '"profile_update":{},"relation_update":{},"retrieval_plan":{"should_retrieve":true,"queries":["q1"],"reason":"ok"}}'
        )
        client = _UnifiedLLMClient(payload)
        decision = client.generate_unified_decision(
            prompt_context=_prompt_ctx(),
            user_message="你好",
            relation_json='{"polarity":"neutral","strength":0}',
            profile_json="{}",
            session_emotion=0.1,
            global_emotion=0.1,
            memory_count=0,
            current_hour=12,
            current_date="2026-01-01",
            current_year=2026,
        )
        assert decision.should_reply is False
        assert decision.reply == ""
        assert decision.skip_reason == "skip:test"
        assert decision.retrieval_plan.queries == ["q1"]
    finally:
        settings.llm.provider = old_provider
        settings.llm.api_key = old_key


def test_generate_unified_decision_fallbacks_on_invalid_payload() -> None:
    old_provider = settings.llm.provider
    old_key = settings.llm.api_key
    settings.llm.provider = "kilo"
    settings.llm.api_key = "test-key"
    try:
        client = _UnifiedLLMClient("not-json")
        decision = client.generate_unified_decision(
            prompt_context=_prompt_ctx(),
            user_message="测试消息",
            relation_json='{"polarity":"neutral","strength":0}',
            profile_json="{}",
            session_emotion=0.1,
            global_emotion=0.1,
            memory_count=0,
            current_hour=12,
            current_date="2026-01-01",
            current_year=2026,
        )
        assert decision.should_reply is True
        assert decision.retrieval_plan.should_retrieve is True
        assert decision.retrieval_plan.queries == ["测试消息"]
    finally:
        settings.llm.provider = old_provider
        settings.llm.api_key = old_key
