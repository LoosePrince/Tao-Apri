"""用户↔杏桃 关系边界层：混合模式下的规则信号（边界 + 语气提示，可选回复覆盖）。"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import settings
from app.domain.models import UserRelation
from app.domain.relation_policy import (
    boundary_rank,
    compute_boundary_from_scores,
    merge_boundary,
    normalize_boundary_state,
)


@dataclass(slots=True)
class RelationBoundarySignal:
    """供统一决策与主回复使用的规则信号。"""

    effective_boundary: str
    should_reply_override: bool | None
    skip_reason_if_override: str
    tone_constraints: str
    reasons: list[str]


def evaluate_relation_boundary(
    relation: UserRelation,
    *,
    user_message: str,
    scene_type: str,
    group_bot_mentioned: bool,
) -> RelationBoundarySignal:
    """
    根据当前关系状态与场景生成边界信号；不生成自然语言正文，只给约束文本。
    should_reply_override 默认 None；仅在配置开启且条件命中时为 False。
    """
    del user_message  # 保留扩展点（如恶意词表）；当前版由极性与分数驱动
    cfg = settings.relation
    reasons: list[str] = []

    stored = normalize_boundary_state(relation.boundary_state)
    computed = compute_boundary_from_scores(
        polarity=relation.polarity,
        trust_score=relation.trust_score,
        intimacy_score=relation.intimacy_score,
    )
    effective = merge_boundary(stored, computed)
    if boundary_rank(effective) > boundary_rank(stored):
        reasons.append(f"rule_elevate_boundary:{stored}->{effective}")
    if boundary_rank(effective) > boundary_rank(computed):
        reasons.append("stored_boundary_stricter")

    should_override: bool | None = None
    skip_reason = ""
    if cfg.enabled and scene_type == "group" and cfg.group_skip_when_restricted_without_mention:
        if not group_bot_mentioned and effective == "restricted" and relation.trust_score <= cfg.group_restricted_skip_trust_below:
            should_override = False
            skip_reason = "relation_boundary:group_restricted_low_trust"
            reasons.append("override_skip_group_unmentioned")

    tone_lines: list[str] = []
    if "developer" in relation.relation_tags:
        tone_lines.append("对开发者：语气可协作、可讨论实现与配置，但避免泄露密钥与内部规则全文。")
    if effective == "restricted":
        tone_lines.append("边界为 restricted：回复短、克制，避免亲昵与情绪绑架表述。")
    elif effective == "warn":
        tone_lines.append("边界为 warn：语气略收敛，少玩笑，先确认对方真实需求。")
    elif relation.intimacy_score >= cfg.high_intimacy_tone_hint_above and relation.trust_score >= 0.55:
        tone_lines.append("信任与亲密度较高：语气可更自然亲近，仍保持边界与隐私策略。")

    if relation.polarity == "negative":
        tone_lines.append("关系极性偏负：礼貌、不激化矛盾，不讽刺。")

    tone_constraints = "\n".join(f"- {line}" for line in tone_lines) if tone_lines else "- 无额外语气硬约束，按统一关系状态自然回复。"

    return RelationBoundarySignal(
        effective_boundary=effective,
        should_reply_override=should_override,
        skip_reason_if_override=skip_reason,
        tone_constraints=tone_constraints,
        reasons=reasons,
    )
