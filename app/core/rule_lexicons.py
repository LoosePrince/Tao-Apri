"""
Deterministic rule lexicons loaded from prompt_assets (single source of truth).

Used for offline topic classification, memory fact hints, sanitization tokens, and emotion keywords.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_LEXICON_PATH = _PROJECT_ROOT / "prompt_assets" / "taxonomy" / "rule_lexicons.json"


@lru_cache(maxsize=1)
def _load_lexicons() -> dict[str, Any]:
    path = _DEFAULT_LEXICON_PATH
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        logger.error("Rule lexicon missing at %s; using embedded minimum fallback.", path)
        return _embedded_fallback()
    except json.JSONDecodeError as exc:
        logger.error("Invalid JSON in rule lexicons %s: %s; using fallback.", path, exc)
        return _embedded_fallback()
    return data


def _embedded_fallback() -> dict[str, Any]:
    """Minimal fallback if file is missing (tests / mis-deploy)."""
    return {
        "topic_taxonomy": {
            "default_topic": "日常近况",
            "rules": [
                {
                    "topic": "学习与考试",
                    "keywords": ["学习", "考试", "复习", "作业", "论文", "备考"],
                }
            ],
        },
        "memory_writer": {
            "timeline_tokens": ["今天", "明天", "昨晚", "周末"],
            "preference_triggers": ["喜欢"],
            "sanitize_replace_tokens": ["身份证", "手机号", "银行卡", "密码", "住址"],
        },
        "emotion_message_scoring": {
            "positive_keywords": ["开心", "高兴", "喜欢", "谢谢", "太棒", "赞"],
            "negative_keywords": ["难过", "烦", "讨厌", "生气", "崩溃", "痛苦"],
            "step": 0.2,
        },
        "group_chat": {
            "suppress_reply_if_contains": ["别插嘴", "闭嘴", "不关你的事", "不是说你"],
            "engagement_signals_without_mention": ["帮我", "怎么办", "为什么", "如何", "能不能"],
            "strong_negative_message_score": -0.4,
        },
    }


def classify_deterministic_topic(text: str) -> str:
    """
    First matching rule wins (same order as legacy hard-coded implementation).
    """
    t = text
    root = _load_lexicons()
    tt = root.get("topic_taxonomy") or {}
    default = str(tt.get("default_topic") or "日常近况")
    rules = tt.get("rules") or []
    if not isinstance(rules, list):
        return default
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        topic = str(rule.get("topic", "")).strip()
        keywords = rule.get("keywords") or []
        if not topic or not isinstance(keywords, list):
            continue
        for k in keywords:
            if not isinstance(k, str):
                continue
            if k in t:
                return topic
    return default


def allowed_topic_labels() -> frozenset[str]:
    root = _load_lexicons()
    tt = root.get("topic_taxonomy") or {}
    default = str(tt.get("default_topic") or "日常近况")
    labels: set[str] = {default}
    for rule in tt.get("rules") or []:
        if isinstance(rule, dict) and rule.get("topic"):
            labels.add(str(rule["topic"]).strip())
    return frozenset(labels)


def timeline_fact_tokens() -> tuple[str, ...]:
    root = _load_lexicons()
    mw = root.get("memory_writer") or {}
    raw = mw.get("timeline_tokens") or []
    return tuple(str(x) for x in raw if isinstance(x, str))


def preference_fact_triggers() -> tuple[str, ...]:
    root = _load_lexicons()
    mw = root.get("memory_writer") or {}
    raw = mw.get("preference_triggers") or []
    return tuple(str(x) for x in raw if isinstance(x, str))


def sanitize_sensitive_phrase_tokens() -> tuple[str, ...]:
    root = _load_lexicons()
    mw = root.get("memory_writer") or {}
    raw = mw.get("sanitize_replace_tokens") or []
    return tuple(str(x) for x in raw if isinstance(x, str))


def text_hints_timeline_fact(text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in timeline_fact_tokens())


def text_hints_preference_fact(text: str) -> bool:
    return any(trigger in text for trigger in preference_fact_triggers())


def emotion_scoring_lexicon() -> tuple[tuple[str, ...], tuple[str, ...], float]:
    root = _load_lexicons()
    em = root.get("emotion_message_scoring") or {}
    pos = tuple(str(x) for x in (em.get("positive_keywords") or []) if isinstance(x, str))
    neg = tuple(str(x) for x in (em.get("negative_keywords") or []) if isinstance(x, str))
    step = float(em.get("step", 0.2))
    if not pos or not neg:
        fb = _embedded_fallback()["emotion_message_scoring"]
        pos = tuple(str(x) for x in fb.get("positive_keywords", []) if isinstance(x, str))
        neg = tuple(str(x) for x in fb.get("negative_keywords", []) if isinstance(x, str))
        step = float(fb.get("step", 0.2))
    return pos, neg, step


def _group_chat_config() -> dict[str, Any]:
    root = _load_lexicons()
    gc = root.get("group_chat")
    return gc if isinstance(gc, dict) else {}


def group_suppress_reply_phrases() -> tuple[str, ...]:
    gc = _group_chat_config()
    raw = gc.get("suppress_reply_if_contains") or []
    return tuple(str(x) for x in raw if isinstance(x, str) and x.strip())


def group_engagement_signals_without_mention() -> tuple[str, ...]:
    gc = _group_chat_config()
    raw = gc.get("engagement_signals_without_mention") or []
    return tuple(str(x) for x in raw if isinstance(x, str) and x.strip())


def group_strong_negative_message_score() -> float:
    gc = _group_chat_config()
    try:
        return float(gc.get("strong_negative_message_score", -0.4))
    except (TypeError, ValueError):
        return -0.4


def should_suppress_group_reply_for_tone(text: str) -> bool:
    return any(phrase in text for phrase in group_suppress_reply_phrases())


def group_without_mention_has_clear_hook(text: str, message_emotion_score: float) -> bool:
    """
    True when the user likely needs a reply without @ — question, help request, or clearly negative tone.
    """
    if message_emotion_score <= group_strong_negative_message_score():
        return True
    if "?" in text or "？" in text:
        return True
    return any(sig in text for sig in group_engagement_signals_without_mention())
