from __future__ import annotations

from app.core.config import settings
from app.services.image_understanding_service import ImageUnderstandingService


class _StubLLMClient:
    def analyze_image_with_vision_model(self, **kwargs) -> str:  # noqa: ANN003
        del kwargs
        return "视觉：这是一张测试图片。"


def test_image_understanding_disabled_returns_empty() -> None:
    old_enabled = settings.image_understanding.enabled
    settings.image_understanding.enabled = False
    try:
        service = ImageUnderstandingService(llm_client=_StubLLMClient())  # type: ignore[arg-type]
        result = service.analyze_attachments([{"type": "image", "data": {"url": "http://example.com/a.png"}}])
        assert result.merged_summary == ""
        assert result.ocr_text == ""
        assert result.vision_text == ""
    finally:
        settings.image_understanding.enabled = old_enabled


def test_image_understanding_vision_only_uses_vision_result() -> None:
    old_image_enabled = settings.image_understanding.enabled
    old_ocr_enabled = settings.ocr.enabled
    old_vision_enabled = settings.vision.enabled
    settings.image_understanding.enabled = True
    settings.ocr.enabled = False
    settings.vision.enabled = True
    try:
        service = ImageUnderstandingService(llm_client=_StubLLMClient())  # type: ignore[arg-type]

        def _fake_read_image_bytes(item, *, max_mb, timeout_seconds):  # noqa: ANN001
            del item, max_mb, timeout_seconds
            return b"fake", "http://example.com/x.png", "image/png"

        service._read_image_bytes = _fake_read_image_bytes  # type: ignore[method-assign]
        result = service.analyze_attachments([{"type": "image", "data": {"url": "http://example.com/x.png"}}])
        assert result.ocr_text == ""
        assert "视觉：这是一张测试图片。" in result.vision_text
        assert "视觉识别" in result.merged_summary
    finally:
        settings.image_understanding.enabled = old_image_enabled
        settings.ocr.enabled = old_ocr_enabled
        settings.vision.enabled = old_vision_enabled


def test_image_understanding_merge_strategy_and_prefer_order_take_effect() -> None:
    old_image_enabled = settings.image_understanding.enabled
    old_ocr_enabled = settings.ocr.enabled
    old_vision_enabled = settings.vision.enabled
    old_strategy = settings.image_understanding.merge_strategy
    old_prefer = settings.image_understanding.prefer_ocr_first
    settings.image_understanding.enabled = True
    settings.ocr.enabled = True
    settings.vision.enabled = True
    settings.image_understanding.merge_strategy = "ocr_plus_vision"
    settings.image_understanding.prefer_ocr_first = False
    try:
        service = ImageUnderstandingService(llm_client=_StubLLMClient())  # type: ignore[arg-type]

        def _fake_read_image_bytes(item, *, max_mb, timeout_seconds):  # noqa: ANN001
            del item, max_mb, timeout_seconds
            return b"fake", "http://example.com/x.png", "image/png"

        service._read_image_bytes = _fake_read_image_bytes  # type: ignore[method-assign]
        service._run_ocr = lambda image_bytes, mime_type: "OCR文本"  # type: ignore[method-assign]
        result = service.analyze_attachments([{"type": "image", "data": {"url": "http://example.com/x.png"}}])
        assert result.merged_summary.startswith("视觉识别：")
    finally:
        settings.image_understanding.enabled = old_image_enabled
        settings.ocr.enabled = old_ocr_enabled
        settings.vision.enabled = old_vision_enabled
        settings.image_understanding.merge_strategy = old_strategy
        settings.image_understanding.prefer_ocr_first = old_prefer


def test_image_understanding_unknown_ocr_engine_skips_ocr() -> None:
    old_image_enabled = settings.image_understanding.enabled
    old_ocr_enabled = settings.ocr.enabled
    old_vision_enabled = settings.vision.enabled
    old_engine = settings.ocr.engine
    settings.image_understanding.enabled = True
    settings.ocr.enabled = True
    settings.vision.enabled = False
    settings.ocr.engine = "unsupported_engine"
    try:
        service = ImageUnderstandingService(llm_client=_StubLLMClient())  # type: ignore[arg-type]
        service._read_image_bytes = lambda item, *, max_mb, timeout_seconds: (  # type: ignore[method-assign]
            b"fake",
            "http://example.com/x.png",
            "image/png",
        )
        result = service.analyze_attachments([{"type": "image", "data": {"url": "http://example.com/x.png"}}])
        assert result.ocr_text == ""
    finally:
        settings.image_understanding.enabled = old_image_enabled
        settings.ocr.enabled = old_ocr_enabled
        settings.vision.enabled = old_vision_enabled
        settings.ocr.engine = old_engine
