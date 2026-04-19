"""
Build low-weight, time-anchored recent transcript snippets for the system prompt.

Messages are strictly older than the current in-flight user batch (not yet persisted).
Optional cross-scope mix: same user's other conversations, not older than the oldest
in-scope row in this reference batch.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.config import settings
from app.domain.models import Message
from app.services.prompt_composer import PromptComposer

_WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
_MAX_BODY_CHARS = 320
_MAX_SCOPE_LABEL = 56


def _aware_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def merge_scope_and_cross_messages(scope_messages: list[Message], cross_messages: list[Message]) -> list[Message]:
    """
    Merge two oldest→newest lists by `created_at` (ties: prefer in-scope row first).
    """
    i = j = 0
    out: list[Message] = []
    while i < len(scope_messages) and j < len(cross_messages):
        si = _aware_utc(scope_messages[i].created_at)
        sj = _aware_utc(cross_messages[j].created_at)
        if si <= sj:
            out.append(scope_messages[i])
            i += 1
        else:
            out.append(cross_messages[j])
            j += 1
    if i < len(scope_messages):
        out.extend(scope_messages[i:])
    if j < len(cross_messages):
        out.extend(cross_messages[j:])
    return out


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


def _source_label(msg: Message, current_scope_id: str) -> str:
    sid = (msg.scope_id or "").strip()
    cur = (current_scope_id or "").strip()
    if sid == cur:
        return "本会话"
    label = sid if len(sid) <= _MAX_SCOPE_LABEL else sid[: _MAX_SCOPE_LABEL - 1] + "…"
    return f"其它会话（{label}）"


def build_history_reference_context(
    *,
    now: datetime,
    messages: list[Message],
    current_scope_id: str,
    viewer_user_id: str = "",
) -> str:
    """
    `messages` chronological (oldest → newest), possibly merged in-scope + cross-scope rows.
    """
    tz = _local_tz()
    tz_label = getattr(tz, "key", settings.app.timezone)
    now_aware = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    now_local = _to_local(now_aware, tz)
    cur = (current_scope_id or "").strip()
    has_cross = any((m.scope_id or "").strip() != cur for m in messages) if cur else False
    n_primary = sum(1 for m in messages if (m.scope_id or "").strip() == cur) if cur else len(messages)
    vu = (viewer_user_id or "").strip()
    has_peer = bool(vu) and any((m.user_id or "").strip() != vu for m in messages)

    header_lines = [
        "### 近期对话参考（低权重 · 仅时空背景）",
        f"- 以下共 {len(messages)} 条，均**早于本轮用户输入**；其中本会话片段 {n_primary} 条（按 `scope` 已落库、最多取满参考窗口）。",
    ]
    if has_cross:
        header_lines.append(
            "- 已启用**参杂**：混入**其它会话**片段（可含同用户其它 scope；若配置开启且当前为群聊，还可含**同群其它成员**发言）；**时间不早于**本会话本批参考里**最旧一条**的时间，用于弱关联上下文，**权重低于**本会话行与本轮 user 原文。"
        )
    if has_cross and has_peer:
        header_lines.append(
            "- 下列中若出现 **发送者** 与当前对谈用户不同，即为同群内其它成员的历史句，仅作氛围参考，**不要**当成对方在私聊里对你说话。"
        )
    header_lines.extend(
        [
            "- 每条同时给出 **绝对时间** 与 **相对「本轮处理时刻」的时间差**；行内 **来源** 标明本会话 vs 其它会话，避免把「当时别处」与「此刻此处」混为一谈。",
            "- **不得**把历史句当成用户现在正在说的新诉求；若与下方「本轮 user 消息」或检索记忆矛盾，以本轮用户原文与记忆块为准。",
            "",
        ]
    )
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
        src = _source_label(msg, cur)
        body_lines.append(
            f"{idx}. [绝对时间] {abs_s} · [相对本轮] 距今 {rel_s} · 来源={src} · 发送者={msg.user_id} · 角色={role_cn}\n   正文：{safe}"
        )

    return "\n".join(header_lines + body_lines) + "\n"
