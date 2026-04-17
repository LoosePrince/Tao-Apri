from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
from urllib.request import urlopen

from app.core.config import settings
from app.services.llm_client import LLMClient


@dataclass(slots=True)
class ImageUnderstandingResult:
    ocr_text: str
    vision_text: str
    merged_summary: str
    errors: list[str]


class ImageUnderstandingService:
    def __init__(self, *, llm_client: LLMClient) -> None:
        self.llm_client = llm_client
        self._rapid_ocr_engine: Any | None = None

    @staticmethod
    def _is_image_attachment(item: dict[str, object]) -> bool:
        return str(item.get("type", "")).strip() == "image"

    @staticmethod
    def _decode_file_uri(uri: str) -> Path:
        parsed = urlparse(uri)
        path = unquote(parsed.path or "")
        if path.startswith("/") and len(path) >= 3 and path[2] == ":":
            path = path[1:]
        return Path(path)

    @staticmethod
    def _guess_mime_type(source: str) -> str:
        mime, _ = mimetypes.guess_type(source)
        return mime or "image/png"

    @staticmethod
    def _merge_texts(ocr_text: str, vision_text: str) -> str:
        strategy = str(settings.image_understanding.merge_strategy or "ocr_plus_vision").strip().lower()
        ocr = ocr_text.strip()
        vision = vision_text.strip()
        if strategy == "ocr_only":
            return f"OCR识别：{ocr}" if ocr else ""
        if strategy == "vision_only":
            return f"视觉识别：{vision}" if vision else ""
        if strategy == "vision_plus_ocr":
            pieces: list[str] = []
            if vision:
                pieces.append(f"视觉识别：{vision}")
            if ocr:
                pieces.append(f"OCR识别：{ocr}")
            return "\n".join(pieces).strip()
        # Default: ocr_plus_vision; allow prefer_ocr_first override.
        prefer_ocr_first = bool(settings.image_understanding.prefer_ocr_first)
        pieces = []
        if prefer_ocr_first:
            if ocr:
                pieces.append(f"OCR识别：{ocr}")
            if vision:
                pieces.append(f"视觉识别：{vision}")
        else:
            if vision:
                pieces.append(f"视觉识别：{vision}")
            if ocr:
                pieces.append(f"OCR识别：{ocr}")
        return "\n".join(pieces).strip()

    @staticmethod
    def _safe_limit_bytes(content: bytes, max_mb: float) -> bytes:
        limit = int(max_mb * 1024 * 1024)
        return content[:limit]

    def _get_rapid_ocr(self) -> Any | None:
        engine = str(settings.ocr.engine or "rapidocr").strip().lower()
        if engine not in {"rapidocr", "rapidocr_onnxruntime"}:
            return None
        if self._rapid_ocr_engine is not None:
            return self._rapid_ocr_engine
        try:
            from rapidocr_onnxruntime import RapidOCR  # type: ignore[reportMissingImports]
        except Exception:
            return None
        self._rapid_ocr_engine = RapidOCR()
        return self._rapid_ocr_engine

    def _read_image_bytes(self, item: dict[str, object], *, max_mb: float, timeout_seconds: float) -> tuple[bytes | None, str, str]:
        data = item.get("data", {}) or {}
        if not isinstance(data, dict):
            return None, "", ""
        url = str(data.get("url", "")).strip()
        file_ref = str(data.get("file", "")).strip()
        if url:
            with urlopen(url, timeout=timeout_seconds) as resp:
                raw = resp.read(int(max_mb * 1024 * 1024) + 1)
            if len(raw) > int(max_mb * 1024 * 1024):
                return None, "", "image_too_large"
            return raw, url, self._guess_mime_type(url)
        if file_ref:
            path = self._decode_file_uri(file_ref) if file_ref.startswith("file://") else Path(file_ref)
            raw = path.read_bytes()
            if len(raw) > int(max_mb * 1024 * 1024):
                return None, "", "image_too_large"
            return self._safe_limit_bytes(raw, max_mb), str(path), self._guess_mime_type(str(path))
        return None, "", "image_source_missing"

    def _run_ocr(self, image_bytes: bytes, mime_type: str) -> str:
        del mime_type
        if not settings.ocr.enabled:
            return ""
        ocr = self._get_rapid_ocr()
        if ocr is None:
            return ""
        result = ocr(image_bytes)
        if not result:
            return ""
        if isinstance(result, tuple):
            # RapidOCR returns (result, elapsed)
            payload = result[0]
        else:
            payload = result
        texts: list[str] = []
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, list) and len(item) >= 2:
                    candidate = item[1]
                    if isinstance(candidate, str):
                        texts.append(candidate.strip())
                    elif isinstance(candidate, tuple) and candidate:
                        texts.append(str(candidate[0]).strip())
        return " ".join(part for part in texts if part)

    def analyze_attachments(self, attachments: list[dict[str, object]]) -> ImageUnderstandingResult:
        if not settings.image_understanding.enabled:
            return ImageUnderstandingResult("", "", "", [])
        errors: list[str] = []
        ocr_texts: list[str] = []
        vision_texts: list[str] = []
        image_items = [item for item in attachments if self._is_image_attachment(item)]
        for item in image_items:
            max_mb = settings.ocr.max_image_mb if settings.ocr.enabled else settings.vision.max_image_mb
            timeout_seconds = (
                settings.ocr.download_timeout_seconds if settings.ocr.enabled else settings.vision.download_timeout_seconds
            )
            try:
                image_bytes, image_source, mime_type = self._read_image_bytes(
                    item,
                    max_mb=max_mb,
                    timeout_seconds=timeout_seconds,
                )
            except Exception as exc:
                errors.append(f"image_read_failed:{exc}")
                continue
            if image_bytes is None:
                if image_source:
                    errors.append(f"image_skipped:{image_source}")
                continue

            if settings.ocr.enabled:
                try:
                    text = self._run_ocr(image_bytes, mime_type)
                    if text:
                        ocr_texts.append(text)
                except Exception as exc:
                    errors.append(f"ocr_failed:{exc}")

            if settings.vision.enabled:
                try:
                    vision_text = self.llm_client.analyze_image_with_vision_model(
                        image_url=image_source if image_source.startswith("http") else None,
                        image_bytes=image_bytes,
                        mime_type=mime_type,
                    )
                    if vision_text:
                        vision_texts.append(vision_text)
                except Exception as exc:
                    errors.append(f"vision_failed:{exc}")

        ocr_text = "\n".join(item for item in ocr_texts if item).strip()
        vision_text = "\n".join(item for item in vision_texts if item).strip()
        merged = self._merge_texts(ocr_text, vision_text)
        return ImageUnderstandingResult(
            ocr_text=ocr_text,
            vision_text=vision_text,
            merged_summary=merged,
            errors=errors,
        )
