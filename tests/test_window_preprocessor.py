from app.core.config import settings
from app.services.window_preprocessor import WindowPreprocessor


class _LLMStub:
    def extract_keywords(self, **kwargs) -> list[str]:  # noqa: ANN003
        return ["关键词1", "关键词2"]

    def summarize_long_message(self, **kwargs) -> str:  # noqa: ANN003
        return "长文本摘要"

    def summarize_window_messages(self, **kwargs) -> str:  # noqa: ANN003
        return "窗口级摘要"


def test_long_text_replaced_with_placeholder() -> None:
    old_single = settings.rhythm.single_message_char_threshold
    settings.rhythm.single_message_char_threshold = 10
    try:
        pre = WindowPreprocessor(llm_client=_LLMStub())  # type: ignore[arg-type]
        result = pre.preprocess(["这是一段非常非常长的文本内容"])
        assert result.long_placeholder_count == 1
        assert "长文本占位" in result.merged_user_message
    finally:
        settings.rhythm.single_message_char_threshold = old_single


def test_window_summary_triggered_when_over_threshold() -> None:
    old_window = settings.rhythm.window_char_threshold
    settings.rhythm.window_char_threshold = 20
    try:
        pre = WindowPreprocessor(llm_client=_LLMStub())  # type: ignore[arg-type]
        result = pre.preprocess(["第一条消息", "第二条消息", "第三条消息"])
        assert result.used_window_summary
        assert result.merged_user_message == "窗口级摘要"
    finally:
        settings.rhythm.window_char_threshold = old_window
