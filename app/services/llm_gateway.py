from __future__ import annotations

import logging
import time

from openai import OpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)


class LLMGateway:
    def __init__(self) -> None:
        self._client: OpenAI | None = None
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0

    def is_circuit_open(self) -> bool:
        return time.monotonic() < self._circuit_open_until

    def on_request_success(self) -> None:
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0

    def on_request_failure(self, exc: Exception) -> None:
        self._consecutive_failures += 1
        threshold = settings.llm.circuit_breaker_failure_threshold
        if self._consecutive_failures >= threshold:
            open_seconds = settings.llm.circuit_breaker_open_seconds
            self._circuit_open_until = time.monotonic() + open_seconds
            logger.error(
                "Gateway circuit opened | failures=%s | open_seconds=%s | reason=%s",
                self._consecutive_failures,
                open_seconds,
                exc,
            )

    def chat_completion(self, *, model: str, temperature: float, messages: list[dict[str, object]]) -> str:
        client = self._get_client()
        response = client.chat.completions.create(
            model=model,
            temperature=temperature,
            messages=messages,
        )
        return (response.choices[0].message.content if response.choices else "") or ""

    def list_models(self) -> list[str]:
        client = self._get_client()
        models = client.models.list()
        return sorted({item.id for item in models.data if getattr(item, "id", None)})

    def _get_client(self) -> OpenAI:
        if self._client is None:
            self._client = OpenAI(
                api_key=settings.llm.api_key,
                base_url=settings.llm.base_url,
                timeout=settings.llm.timeout_seconds,
            )
        return self._client
