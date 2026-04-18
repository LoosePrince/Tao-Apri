from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, UTC
from typing import Any

from app.core.config import settings
from app.domain.conversation_scope import ConversationScope
from app.repos.interfaces import MessageRepo, VectorRepo
from app.services.channel_sender import ChannelRouter, SendMessageRequest
from app.services.retrieval_policy_service import RetrievalPolicyService
from app.tool_runtime.audit import SendRateLimiter
from app.tool_runtime.types import ToolResult, ToolSpec


@dataclass(slots=True)
class SearchMemoryTool:
    vector_repo: VectorRepo
    retrieval_policy_service: RetrievalPolicyService
    viewer_scope: ConversationScope

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="search_memory",
            description="搜索记忆并返回可见结果",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
                    "min_score": {"type": "number", "minimum": 0, "maximum": 1},
                    "recency_days": {"type": "integer", "minimum": 1, "maximum": 365},
                },
                "required": ["query"],
            },
            read_only=True,
            concurrency_safe=True,
        )

    def call(self, payload: dict[str, Any]) -> ToolResult:
        query = str(payload.get("query", "")).strip()
        if not query:
            return ToolResult(tool_name="search_memory", call_id="", ok=False, error="query is required")
        top_k = int(payload.get("top_k") or settings.retrieval.top_k)
        min_score = float(payload.get("min_score") or settings.retrieval.min_score)
        recency_days = int(payload.get("recency_days") or settings.retrieval.recency_window_days)
        memories = self.vector_repo.search(
            query=query,
            user_id=self.viewer_scope.actor_user_id,
            limit=max(1, min(20, top_k)),
            min_score=max(0.0, min(1.0, min_score)),
            recency_window_days=max(1, min(365, recency_days)),
        )
        visible, policy_stats = self.retrieval_policy_service.apply(viewer=self.viewer_scope, memories=memories)
        rows = [
            {
                "message_id": m.message_id,
                "user_id": m.user_id,
                "role": m.role,
                "session_id": m.session_id,
                "scope_id": m.scope_id,
                "content": m.sanitized_content,
                "created_at": m.created_at.isoformat(),
                "retrieval_meta": m.retrieval_meta,
            }
            for m in visible
        ]
        return ToolResult(tool_name="search_memory", call_id="", ok=True, data={"hits": rows, "policy_stats": policy_stats})


@dataclass(slots=True)
class QueryMessagesTool:
    message_repo: MessageRepo

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="query_messages",
            description="查询历史消息",
            input_schema={
                "type": "object",
                "properties": {
                    "user_id": {"type": "string"},
                    "session_id": {"type": "string"},
                    "scope_id": {"type": "string"},
                    "source_message_id": {"type": "string"},
                    "role": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                },
            },
            read_only=True,
            concurrency_safe=True,
        )

    def call(self, payload: dict[str, Any]) -> ToolResult:
        limit = int(payload.get("limit") or 30)
        all_rows = self.message_repo.list_all(limit=max(1, min(500, limit * 4)))
        user_id = str(payload.get("user_id", "")).strip()
        session_id = str(payload.get("session_id", "")).strip()
        scope_id = str(payload.get("scope_id", "")).strip()
        source_message_id = str(payload.get("source_message_id", "")).strip()
        role = str(payload.get("role", "")).strip()

        rows = all_rows
        if user_id:
            rows = [item for item in rows if item.user_id == user_id]
        if session_id:
            rows = [item for item in rows if item.session_id == session_id]
        if scope_id:
            rows = [item for item in rows if item.scope_id == scope_id]
        if source_message_id:
            rows = [item for item in rows if (item.source_message_id or "").strip() == source_message_id]
        if role:
            rows = [item for item in rows if item.role == role]
        rows = rows[-max(1, min(200, limit)) :]

        data = [
            {
                "message_id": item.message_id,
                "user_id": item.user_id,
                "role": item.role,
                "session_id": item.session_id,
                "scope_id": item.scope_id,
                "content": item.sanitized_content,
                "created_at": item.created_at.isoformat(),
                "source_message_id": item.source_message_id,
            }
            for item in rows
        ]
        return ToolResult(tool_name="query_messages", call_id="", ok=True, data={"messages": data})


@dataclass(slots=True)
class SendMessageTool:
    router: ChannelRouter
    rate_limiter: SendRateLimiter

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="send_message",
            description="向指定渠道目标发送消息",
            input_schema={
                "type": "object",
                "properties": {
                    "channel": {"type": "string"},
                    "target_type": {"type": "string", "enum": ["private", "group"]},
                    "target_id": {"type": "string"},
                    "content": {"type": "string"},
                    "idempotency_key": {"type": "string"},
                },
                "required": ["channel", "target_type", "target_id", "content"],
            },
            read_only=False,
            concurrency_safe=False,
        )

    def call(self, payload: dict[str, Any]) -> ToolResult:
        if not self.rate_limiter.allow():
            return ToolResult(tool_name="send_message", call_id="", ok=False, error="send rate limit exceeded")
        channel = str(payload.get("channel", "")).strip().lower()
        target_type = str(payload.get("target_type", "")).strip().lower()
        target_id = str(payload.get("target_id", "")).strip()
        content = str(payload.get("content", "")).strip()
        idempotency_key = str(payload.get("idempotency_key", "")).strip() or datetime.now(UTC).isoformat()
        if not channel or not target_type or not target_id or not content:
            return ToolResult(tool_name="send_message", call_id="", ok=False, error="missing required fields")

        key = f"{channel}:{target_type}:{target_id}"
        if settings.tools.force_send_whitelist and key not in set(settings.tools.allowed_send_targets):
            return ToolResult(tool_name="send_message", call_id="", ok=False, error="target not in whitelist")

        request = SendMessageRequest(
            channel=channel,
            target_type=target_type,
            target_id=target_id,
            content=content,
            idempotency_key=idempotency_key,
        )
        try:
            platform_message_id = self.router.send(request)
        except Exception as exc:
            return ToolResult(tool_name="send_message", call_id="", ok=False, error=str(exc))
        return ToolResult(
            tool_name="send_message",
            call_id="",
            ok=True,
            data={"platform_message_id": platform_message_id, "target": key},
        )
