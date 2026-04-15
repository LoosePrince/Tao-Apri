from __future__ import annotations

from dataclasses import dataclass

from app.core.config import settings
from app.services.llm_client import LLMClient


@dataclass(slots=True)
class PreprocessResult:
    merged_user_message: str
    used_window_summary: bool
    long_placeholder_count: int
    window_chars: int
    window_tokens: int


class WindowPreprocessor:
    def __init__(self, *, llm_client: LLMClient) -> None:
        self.llm_client = llm_client

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return max(1, len(text) // 2)

    def preprocess(self, user_messages: list[str]) -> PreprocessResult:
        sanitized = [msg.strip() for msg in user_messages if msg.strip()]
        deduplicated: list[str] = []
        last = ""
        for item in sanitized:
            if item == last:
                continue
            deduplicated.append(item)
            last = item

        long_placeholder_count = 0
        replaced_messages: list[str] = []
        for item in deduplicated:
            chars = len(item)
            tokens = self._estimate_tokens(item)
            is_long = (
                chars >= settings.rhythm.single_message_char_threshold
                or tokens >= settings.rhythm.single_message_token_threshold
            )
            if not is_long:
                replaced_messages.append(item)
                continue
            keywords = self.llm_client.extract_keywords(text=item, top_k=5)
            brief = self.llm_client.summarize_long_message(text=item)
            replaced_messages.append(
                f"[长文本占位] 原长度{chars}字/{tokens}tokens；关键词：{'、'.join(keywords) if keywords else '无'}；摘要：{brief}"
            )
            long_placeholder_count += 1

        merged = "\n".join(f"- {item}" for item in replaced_messages)
        window_chars = len(merged)
        window_tokens = self._estimate_tokens(merged)
        need_window_summary = (
            window_chars >= settings.rhythm.window_char_threshold
            or window_tokens >= settings.rhythm.window_token_threshold
        )
        if need_window_summary:
            merged = self.llm_client.summarize_window_messages(messages=replaced_messages)
        return PreprocessResult(
            merged_user_message=merged,
            used_window_summary=need_window_summary,
            long_placeholder_count=long_placeholder_count,
            window_chars=window_chars,
            window_tokens=window_tokens,
        )
