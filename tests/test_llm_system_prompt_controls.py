from app.services.llm_client import LLMClient
from app.services.prompt_composer import PromptContext


def _prompt_context() -> PromptContext:
    return PromptContext(
        system_core="core",
        system_runtime="runtime",
        memory_context="memory",
        policy_notice="notice",
        parameter_context="param_ctx",
        profile_context="profile",
        user_message="hello",
    )


def test_build_system_prompt_includes_notice_on_first_turn() -> None:
    rendered = LLMClient._build_system_prompt(_prompt_context(), include_notice=True)
    assert "notice" in rendered
    assert "param_ctx" in rendered


def test_build_system_prompt_omits_notice_when_disabled() -> None:
    rendered = LLMClient._build_system_prompt(_prompt_context(), include_notice=False)
    assert "notice" not in rendered
    assert "本轮不注入首轮策略提示。" in rendered
    assert "param_ctx" in rendered
