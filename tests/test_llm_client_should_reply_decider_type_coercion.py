from __future__ import annotations

from app.services.llm_client import LLMClient, ShouldReplyDecision


class _ShouldReplyAsStringLLMClient(LLMClient):
    def __init__(self, payload: dict[str, object]) -> None:
        super().__init__()
        self._payload = payload

    def _call_json_decider(  # type: ignore[override]
        self, *, system_asset: str, user_asset: str, values: dict[str, object]
    ) -> dict[str, object]:
        del system_asset, user_asset, values
        return dict(self._payload)


def test_decide_should_reply_coerces_false_string() -> None:
    client = _ShouldReplyAsStringLLMClient({"should_reply": "false", "reason": "stub"})
    decision: ShouldReplyDecision = client.decide_should_reply(
        user_message="hi",
        session_emotion=0.0,
        global_emotion=0.0,
        fatigue_level=0.0,
        emotion_peak_level=0.0,
        memory_count=0,
        current_hour=12,
        current_date="2026-01-01",
        current_year=2026,
    )
    assert decision.should_reply is False
    assert decision.reason == "stub"


def test_decide_should_reply_coerces_true_string() -> None:
    client = _ShouldReplyAsStringLLMClient({"should_reply": "true", "reason": "stub"})
    decision = client.decide_should_reply(
        user_message="hi",
        session_emotion=0.0,
        global_emotion=0.0,
        fatigue_level=0.0,
        emotion_peak_level=0.0,
        memory_count=0,
        current_hour=12,
        current_date="2026-01-01",
        current_year=2026,
    )
    assert decision.should_reply is True
    assert decision.reason == "stub"


def test_decide_should_reply_unknown_string_defaults_true() -> None:
    client = _ShouldReplyAsStringLLMClient({"should_reply": "maybe", "reason": "stub"})
    decision = client.decide_should_reply(
        user_message="hi",
        session_emotion=0.0,
        global_emotion=0.0,
        fatigue_level=0.0,
        emotion_peak_level=0.0,
        memory_count=0,
        current_hour=12,
        current_date="2026-01-01",
        current_year=2026,
    )
    assert decision.should_reply is True
    assert decision.reason == "stub"

