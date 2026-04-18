"""关系标签与边界状态的归一化与校验（纯函数，无 I/O）。"""

from __future__ import annotations

from app.core.config import settings
from app.domain.models import UserRelation


_BOUNDARY_ORDER = {"normal": 0, "warn": 1, "restricted": 2}


def boundary_rank(state: str) -> int:
    return _BOUNDARY_ORDER.get(str(state).strip().lower(), 0)


def merge_boundary(a: str, b: str) -> str:
    """取更严格的一侧。"""
    ra, rb = boundary_rank(a), boundary_rank(b)
    if rb > ra:
        return _rank_to_boundary(rb)
    return _rank_to_boundary(ra)


def _rank_to_boundary(rank: int) -> str:
    for name, r in _BOUNDARY_ORDER.items():
        if r == rank:
            return name
    return "normal"


def normalize_relation_tags(raw: object, *, allowed: frozenset[str] | None = None) -> list[str]:
    allow = allowed or frozenset(settings.relation.allowed_tags)
    items: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            s = str(item).strip().lower()
            if s and s in allow and s not in items:
                items.append(s)
    elif isinstance(raw, str) and raw.strip():
        s = raw.strip().lower()
        if s in allow and s not in items:
            items.append(s)
    return items


def normalize_role_priority(raw: object, *, allowed: frozenset[str] | None = None) -> str:
    allow = allowed or frozenset(settings.relation.role_priority_allowed)
    s = str(raw or "").strip().lower() or settings.relation.default_role_priority
    return s if s in allow else settings.relation.default_role_priority


def normalize_boundary_state(raw: object) -> str:
    s = str(raw or "").strip().lower()
    if s in _BOUNDARY_ORDER:
        return s
    return settings.relation.default_boundary_state


def compute_boundary_from_scores(
    *,
    polarity: str,
    trust_score: float,
    intimacy_score: float,
) -> str:
    """由数值与极性推导规则层边界（与配置阈值配合）。"""
    del intimacy_score  # 语气侧在 relation_boundary 使用；边界以 trust + polarity 为主
    cfg = settings.relation
    pol = str(polarity or "").strip().lower()
    t = float(trust_score)
    if t <= cfg.boundary_restricted_trust_below:
        return "restricted"
    if pol == "negative" and cfg.restricted_on_negative_polarity and t <= cfg.boundary_warn_trust_below:
        return "restricted"
    if pol == "negative" and t <= cfg.boundary_warn_trust_below:
        return "warn"
    if t <= cfg.boundary_warn_trust_below:
        return "warn"
    return "normal"


def clamp_boundary_with_rules(stored: str, computed: str) -> str:
    """持久化前：规则层不得低于存储层宽松度，取更严。"""
    return merge_boundary(stored, computed)


def ensure_developer_tag(relation: UserRelation, *, user_id: str) -> UserRelation:
    """配置中的开发者账号始终携带 developer 标签。"""
    uid = str(user_id).strip()
    if not uid:
        return relation
    dev_ids = {str(x).strip() for x in settings.relation.developer_user_ids if str(x).strip()}
    if uid not in dev_ids:
        return relation
    if "developer" not in relation.relation_tags:
        relation.relation_tags = [*relation.relation_tags, "developer"]
    if relation.role_priority in ("", "neutral") and settings.relation.promote_developer_role_priority:
        relation.role_priority = "developer"
    return relation


def relation_to_payload_dict(relation: UserRelation) -> dict[str, object]:
    return {
        "polarity": relation.polarity,
        "strength": relation.strength,
        "trust_score": relation.trust_score,
        "intimacy_score": relation.intimacy_score,
        "dependency_score": relation.dependency_score,
        "relation_tags": list(relation.relation_tags),
        "role_priority": relation.role_priority,
        "boundary_state": relation.boundary_state,
    }


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def apply_numeric_and_tags_from_decision(relation: UserRelation, decision: dict[str, object]) -> None:
    """将 LLM 输出的 relation 片段合并到实体；缺省字段保留原值。"""
    if not decision:
        return
    if "polarity" in decision:
        pol = str(decision.get("polarity", "")).strip().lower()
        if pol in {"positive", "neutral", "negative"}:
            relation.polarity = pol
    for key in ("strength", "trust_score", "intimacy_score", "dependency_score"):
        if key in decision and decision[key] is not None:
            try:
                setattr(relation, key, _clamp01(float(decision[key])))
            except (TypeError, ValueError):
                pass
    if "relation_tags" in decision and decision["relation_tags"] is not None:
        relation.relation_tags = normalize_relation_tags(decision["relation_tags"])
    if "role_priority" in decision and decision["role_priority"] is not None:
        relation.role_priority = normalize_role_priority(decision["role_priority"])
    if "boundary_state" in decision and decision["boundary_state"] is not None:
        relation.boundary_state = normalize_boundary_state(decision["boundary_state"])


def finalize_relation_after_update(relation: UserRelation, *, user_id: str) -> None:
    """分数裁剪、规则边界兜底、标签归一、开发者账号标签注入。"""
    relation.strength = _clamp01(relation.strength)
    relation.trust_score = _clamp01(relation.trust_score)
    relation.intimacy_score = _clamp01(relation.intimacy_score)
    relation.dependency_score = _clamp01(relation.dependency_score)
    relation.relation_tags = normalize_relation_tags(relation.relation_tags)
    relation.role_priority = normalize_role_priority(relation.role_priority)
    stored_b = normalize_boundary_state(relation.boundary_state)
    computed_b = compute_boundary_from_scores(
        polarity=relation.polarity,
        trust_score=relation.trust_score,
        intimacy_score=relation.intimacy_score,
    )
    relation.boundary_state = clamp_boundary_with_rules(stored_b, computed_b)
    ensure_developer_tag(relation, user_id=user_id)
