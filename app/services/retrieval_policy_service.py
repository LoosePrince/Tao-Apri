from __future__ import annotations

from dataclasses import dataclass

from app.core.config import settings
from app.domain.conversation_scope import ConversationScope
from app.domain.models import Message
from app.repos.interfaces import PreferenceRepo, RelationRepo


@dataclass(frozen=True, slots=True)
class RetrievalPolicyDecision:
    exposure: str  # deny | summary | redacted_snippet | full
    source_label: str  # self | same_user | same_group_other | cross_scope_other
    reason: str


class RetrievalPolicyService:
    """
    Relation-driven, deterministic retrieval gating.

    This is intentionally conservative: it prefers deny/summary over leaking
    identifiable raw content across users/scopes.
    """

    def __init__(self, *, relation_repo: RelationRepo, preference_repo: PreferenceRepo) -> None:
        self.relation_repo = relation_repo
        self.preference_repo = preference_repo

    @staticmethod
    def _classify_topic(text: str) -> str:
        t = text
        if any(k in t for k in ("学习", "考试", "复习", "作业", "论文", "备考")):
            return "学习与考试"
        if any(k in t for k in ("工作", "职业", "加班", "项目", "会议", "汇报", "同事", "岗位", "面试")):
            return "工作与职业"
        if any(k in t for k in ("睡", "作息", "健康", "锻炼", "运动", "早睡", "晚睡", "饮食", "感冒", "头痛")):
            return "作息与健康"
        if any(k in t for k in ("难过", "焦虑", "生气", "愤怒", "伤心", "烦", "绝望", "开心", "喜欢", "讨厌", "关系")):
            return "情绪与关系"
        if any(k in t for k in ("娱乐", "兴趣", "电影", "音乐", "游戏", "看剧", "旅游")):
            return "娱乐与兴趣"
        return "日常近况"

    def decide(self, *, viewer: ConversationScope, memory: Message) -> RetrievalPolicyDecision:
        # Same scope: always allow (sanitized) full snippet.
        if memory.scope_id and memory.scope_id == viewer.scope_id:
            return RetrievalPolicyDecision(exposure="full", source_label="self", reason="same_scope")

        # Same actor across scopes: allow full (user-owned memory).
        if memory.user_id == viewer.actor_user_id:
            return RetrievalPolicyDecision(exposure="full", source_label="same_user", reason="same_user")

        # Cross-user: check target user's sharing preference.
        pref = self.preference_repo.get(memory.user_id)
        if not pref or pref.share_default != "allow":
            return RetrievalPolicyDecision(exposure="deny", source_label="cross_scope_other", reason="preference_deny")

        # Relation gating.
        rel = self.relation_repo.get(viewer.actor_user_id, memory.user_id)
        if rel and rel.polarity == "negative":
            return RetrievalPolicyDecision(exposure="deny", source_label="cross_scope_other", reason="relation_negative")
        strength = rel.strength if rel else 0.0
        trust = rel.trust_score if rel else 0.0
        if strength < settings.retrieval.relation_access_min_strength:
            return RetrievalPolicyDecision(exposure="deny", source_label="cross_scope_other", reason="relation_weak")

        # Topic deny is an explicit hard deny.
        if pref.topic_visibility:
            topic = self._classify_topic(memory.sanitized_content)
            if pref.topic_visibility.get(topic) == "deny":
                return RetrievalPolicyDecision(exposure="deny", source_label="cross_scope_other", reason="topic_deny")

        # Same group other: allow more than cross-group by default.
        if (
            viewer.scene_type == "group"
            and memory.scene_type == "group"
            and viewer.group_id
            and memory.group_id
            and viewer.group_id == memory.group_id
        ):
            if strength >= settings.retrieval.cross_positive_threshold and trust >= 0.6:
                return RetrievalPolicyDecision(
                    exposure="redacted_snippet",
                    source_label="same_group_other",
                    reason="same_group_high_trust",
                )
            if strength >= settings.retrieval.cross_neutral_threshold:
                return RetrievalPolicyDecision(
                    exposure="summary",
                    source_label="same_group_other",
                    reason="same_group_summary",
                )
            return RetrievalPolicyDecision(exposure="deny", source_label="same_group_other", reason="same_group_relation_low")

        # Cross scope other (private <-> group, cross-group): conservative.
        if strength >= settings.retrieval.cross_neutral_threshold and trust >= 0.8:
            return RetrievalPolicyDecision(exposure="summary", source_label="cross_scope_other", reason="cross_scope_summary")
        if strength >= settings.retrieval.cross_negative_threshold:
            return RetrievalPolicyDecision(
                exposure="redacted_snippet",
                source_label="cross_scope_other",
                reason="cross_scope_redacted_snippet",
            )
        return RetrievalPolicyDecision(exposure="deny", source_label="cross_scope_other", reason="cross_scope_default_deny")

    def apply(self, *, viewer: ConversationScope, memories: list[Message]) -> tuple[list[Message], dict[str, int]]:
        out: list[Message] = []
        stats = {"deny": 0, "summary": 0, "redacted_snippet": 0, "full": 0}
        for mem in memories:
            decision = self.decide(viewer=viewer, memory=mem)
            stats[decision.exposure] = stats.get(decision.exposure, 0) + 1
            if decision.exposure == "deny":
                continue
            meta = dict(mem.retrieval_meta or {})
            meta.update(
                {
                    "exposure": decision.exposure,
                    "source_label": decision.source_label,
                    "policy_reason": decision.reason,
                    "topic": self._classify_topic(mem.sanitized_content),
                }
            )
            mem.retrieval_meta = meta
            out.append(mem)
        return out, stats

