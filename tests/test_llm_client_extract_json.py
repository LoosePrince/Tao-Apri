from __future__ import annotations

from app.services.llm_client import LLMClient


def test_extract_json_picks_first_balanced_object() -> None:
    raw = 'prefix {"should_reply": false, "reason":"a"} suffix {"should_reply": true, "reason":"b"}'
    parsed = LLMClient._extract_json(raw)
    assert parsed.get("should_reply") is False
    assert parsed.get("reason") == "a"


def test_extract_json_handles_noise_and_incomplete_after_first() -> None:
    raw = 'noise {"should_reply": false, "reason":"a"} more noise {"should_reply": true, "reason":"b"'
    parsed = LLMClient._extract_json(raw)
    assert parsed.get("should_reply") is False
    assert parsed.get("reason") == "a"


def test_extract_json_ignores_braces_in_string() -> None:
    raw = 'noise {"reason":"brace } inside","should_reply":true}'
    parsed = LLMClient._extract_json(raw)
    assert parsed.get("should_reply") is True
    assert parsed.get("reason") == "brace } inside"


def test_extract_json_returns_empty_on_non_object_json() -> None:
    raw = 'noise ["a","b"] more noise'
    parsed = LLMClient._extract_json(raw)
    assert parsed == {}

