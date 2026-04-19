"""
Build low-weight, time-anchored recent transcript snippets for the system prompt.

Messages are strictly older than the current in-flight user batch (not yet persisted).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.config import settings
from app.domain.models import Message
from app.services.prompt_composer import PromptComposer

_WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
_MAX_BODY_CHARS = 320


def _local_tz() -> ZoneInfo | timezone:
    name = (settings.app.timezone or "Asia/Shanghai").strip()
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return datetime.now(timezone.utc).astimezone().tzinfo or timezone.utc


def _to_local(dt: datetime, tz: ZoneInfo | timezone) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz)


def _format_absolute(dt_local: datetime, tz_label: str) -> str:
    wd = _WEEKDAYS[dt_local.isoweekday() - 1]
    return f"{dt_local:%Y-%m-%d} {wd} {dt_local:%H:%M:%S}（本地 {tz_label}）"


def _format_relative_age(delta: timedelta) -> str:
    total = max(0, int(delta.total_seconds()))
    if total < 45:
        return "不足 1 分钟"
    if total < 3600:
        m = max(1, total // 60)
        return f"约 {m} 分钟"
    if total < 86400:
        h, r = divmod(total, 3600)
        m = r // 60
        if m:
            return f"约 {h} 小时 {m} 分钟"
        return f"约 {h} 小时"
    d, r = divmod(total, 86400)
    h = r // 3600
    if h:
        return f"约 {d} 天 {h} 小时"
    return f"约 {d} 天"


def build_history_reference_context(*, now: datetime, messages: list[Message]) -> str:
    """
    `messages` must be chronological (oldest → newest), same `scope_id` as the active turn.
    """
    tz = _local_tz()
    tz_label = getattr(tz, "key", settings.app.timezone)
    now_aware = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    now_local = _to_local(now_aware, tz)

    header_lines = [
        "### 近期对话参考（低权重 · 仅时空背景）",
        f"- 以下逐条均**早于本轮用户输入**（来自当前会话 `scope` 已落库消息，最多 {len(messages)} 条）。",
        "- 每条同时给出 **绝对时间** 与 **相对「本轮处理时刻」的时间差**，用于区分「当时」与「此刻」；**不得**把历史句当成用户现在正在说的新诉求。",
        "- 若与下方「本轮 user 消息」或检索记忆矛盾，以本轮用户原文与记忆块为准。",
        "",
    ]
    if not messages:
        return "\n".join(header_lines + ["_（当前尚无更早的已存消息。）_", ""])

    body_lines: list[str] = []
    for idx, msg in enumerate(messages, start=1):
        created_local = _to_local(msg.created_at, tz)
        delta = now_local - created_local
        safe = PromptComposer._redact_identifiable_detail(msg.sanitized_content or "").strip()
        if len(safe) > _MAX_BODY_CHARS:
            safe = safe[: _MAX_BODY_CHARS] + "…"
        role_cn = "用户" if msg.role == "user" else "杏桃" if msg.role == "assistant" else msg.role
        abs_s = _format_absolute(created_local, tz_label)
        rel_s = _format_relative_age(delta)
        body_lines.append(
            f"{idx}. [绝对时间] {abs_s} · [相对本轮] 距今 {rel_s} · 发送者={msg.user_id} · 角色={role_cn}\n   正文：{safe}"
        )

    return "\n".join(header_lines + body_lines) + "\n"
