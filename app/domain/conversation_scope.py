from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ConversationScope:
    """
    Unified conversation context across private chat and group chat.

    scope_id is the primary key for window aggregation and session tracking.
    """

    platform: str
    scene_type: str  # "private" | "group"
    scope_id: str
    actor_user_id: str
    group_id: str | None = None

    @staticmethod
    def private(*, platform: str, user_id: str) -> "ConversationScope":
        uid = str(user_id)
        return ConversationScope(
            platform=platform,
            scene_type="private",
            scope_id=f"private:{uid}",
            actor_user_id=uid,
            group_id=None,
        )

    @staticmethod
    def group(*, platform: str, group_id: str, user_id: str) -> "ConversationScope":
        gid = str(group_id)
        uid = str(user_id)
        return ConversationScope(
            platform=platform,
            scene_type="group",
            scope_id=f"group:{gid}:user:{uid}",
            actor_user_id=uid,
            group_id=gid,
        )

