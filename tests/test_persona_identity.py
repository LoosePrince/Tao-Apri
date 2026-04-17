from datetime import datetime

from app.core.markdown_assets import read_required_markdown_asset
from app.domain.services.persona_engine import PersonaEngine


def test_runtime_persona_matches_identity_roster_for_developer() -> None:
    engine = PersonaEngine()

    persona = engine.get_runtime_persona(datetime(2026, 4, 17, 10, 0, 0), "1377820366")

    assert "身份识别（预置名单）" in persona.identity_context
    assert "developer" in persona.identity_context
    assert "LoosePrince" in persona.identity_context


def test_runtime_persona_identity_context_empty_for_unknown_user() -> None:
    engine = PersonaEngine()

    persona = engine.get_runtime_persona(datetime(2026, 4, 17, 10, 0, 0), "unknown-user")

    assert persona.identity_context == ""


def test_self_awareness_is_real_ai_identity_not_human_roleplay() -> None:
    text = read_required_markdown_asset("persona/self_awareness.md")

    assert "Tao Apri" in text
    assert "我是杏桃" in text
    assert "AI还是人类" in text
    assert "高中生" not in text
