from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4
import json
import re
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.config import settings
from app.domain.conversation_scope import ConversationScope
from app.domain.models import DelayedTask
from app.repos.interfaces import DelayedTaskRepo, MessageRepo, VectorRepo
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


@dataclass(slots=True)
class ScheduleDelayedTaskTool:
    delayed_task_repo: DelayedTaskRepo
    viewer_scope: ConversationScope

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="schedule_delayed_task",
            description="创建延时任务，在指定时间触发 AI 执行",
            input_schema={
                "type": "object",
                "properties": {
                    "time": {"type": "string"},
                    "description": {"type": "string"},
                    "reason": {"type": "string"},
                    "trigger_source": {"type": "string"},
                    "task_payload": {"type": "object"},
                },
                "required": ["time", "description", "reason", "trigger_source"],
            },
            read_only=False,
            concurrency_safe=False,
        )

    @staticmethod
    def _configured_timezone() -> ZoneInfo:
        try:
            return ZoneInfo(settings.app.timezone)
        except ZoneInfoNotFoundError:
            return ZoneInfo("UTC")

    @classmethod
    def _parse_run_at(cls, *, time_expr: str) -> datetime:
        expr = time_expr.strip()
        if not expr:
            raise ValueError("time is required")
        relative = re.fullmatch(r"(?i)\s*(\d+)\s*([smhd])\s*", expr)
        if relative:
            value = int(relative.group(1))
            unit = relative.group(2).lower()
            seconds_map = {"s": 1, "m": 60, "h": 3600, "d": 86400}
            return datetime.now(UTC) + timedelta(seconds=value * seconds_map[unit])
        try:
            parsed_local = datetime.strptime(expr, "%Y.%m.%d %H:%M:%S")
        except ValueError as exc:
            raise ValueError(
                "invalid time format. use relative like 2h/30m/45s/1d "
                "or absolute like 2026.4.18 17:23:59 (interpreted in configured timezone)."
            ) from exc
        tz = cls._configured_timezone()
        return parsed_local.replace(tzinfo=tz).astimezone(UTC)

    def call(self, payload: dict[str, Any]) -> ToolResult:
        description = str(payload.get("description", "")).strip()
        reason = str(payload.get("reason", "")).strip()
        trigger_source = str(payload.get("trigger_source", "")).strip()
        if not description or not reason or not trigger_source:
            return ToolResult(
                tool_name="schedule_delayed_task",
                call_id="",
                ok=False,
                error="description, reason and trigger_source are required",
            )
        try:
            run_at = self._parse_run_at(time_expr=str(payload.get("time", "")).strip())
        except Exception as exc:
            return ToolResult(tool_name="schedule_delayed_task", call_id="", ok=False, error=str(exc))

        raw_payload = payload.get("task_payload")
        task_payload = raw_payload if isinstance(raw_payload, dict) else {}
        if "user_id" not in task_payload:
            task_payload["user_id"] = self.viewer_scope.actor_user_id
        if "scene_type" not in task_payload:
            task_payload["scene_type"] = self.viewer_scope.scene_type
        if "group_id" not in task_payload and self.viewer_scope.group_id:
            task_payload["group_id"] = self.viewer_scope.group_id
        if "platform" not in task_payload:
            task_payload["platform"] = self.viewer_scope.platform
        task_payload.setdefault("trigger_source", trigger_source)
        task_payload.setdefault("reason", reason)

        task = DelayedTask(
            task_id=str(uuid4()),
            run_at=run_at,
            status="pending",
            description=description,
            reason=reason,
            trigger_source=trigger_source,
            payload_json=json.dumps(task_payload, ensure_ascii=False),
            scope_id=self.viewer_scope.scope_id,
            max_attempts=settings.delayed_task.max_attempts,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        created = self.delayed_task_repo.enqueue(task)
        return ToolResult(
            tool_name="schedule_delayed_task",
            call_id="",
            ok=True,
            data={
                "task_id": created.task_id,
                "status": created.status,
                "scheduled_at": datetime.now(UTC).isoformat(),
                "normalized_run_at": created.run_at.isoformat(),
            },
        )


@dataclass(slots=True)
class CancelDelayedTaskTool:
    delayed_task_repo: DelayedTaskRepo
    viewer_scope: ConversationScope

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="cancel_delayed_task",
            description="取消指定延时任务",
            input_schema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                },
                "required": ["task_id"],
            },
            read_only=False,
            concurrency_safe=False,
        )

    def call(self, payload: dict[str, Any]) -> ToolResult:
        task_id = str(payload.get("task_id", "")).strip()
        if not task_id:
            return ToolResult(tool_name="cancel_delayed_task", call_id="", ok=False, error="task_id is required")
        existing = self.delayed_task_repo.get(task_id)
        if existing is None:
            return ToolResult(tool_name="cancel_delayed_task", call_id="", ok=False, error="task not found")
        if existing.scope_id and existing.scope_id != self.viewer_scope.scope_id:
            return ToolResult(tool_name="cancel_delayed_task", call_id="", ok=False, error="task scope mismatch")
        self.delayed_task_repo.cancel(task_id)
        return ToolResult(
            tool_name="cancel_delayed_task",
            call_id="",
            ok=True,
            data={"task_id": task_id, "status": "cancelled"},
        )


@dataclass(slots=True)
class QueryDelayedTasksTool:
    delayed_task_repo: DelayedTaskRepo
    viewer_scope: ConversationScope

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="query_delayed_tasks",
            description="查询当前会话或指定范围的延时任务",
            input_schema={
                "type": "object",
                "properties": {
                    "scope_id": {"type": "string"},
                    "status": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                },
            },
            read_only=True,
            concurrency_safe=True,
        )

    def call(self, payload: dict[str, Any]) -> ToolResult:
        scope_id = str(payload.get("scope_id", "")).strip() or self.viewer_scope.scope_id
        status = str(payload.get("status", "")).strip() or None
        limit = int(payload.get("limit") or 20)
        tasks = self.delayed_task_repo.list_tasks(
            scope_id=scope_id,
            status=status,
            limit=max(1, min(200, limit)),
        )
        rows = [
            {
                "task_id": task.task_id,
                "scope_id": task.scope_id,
                "status": task.status,
                "run_at": task.run_at.isoformat(),
                "description": task.description,
                "reason": task.reason,
                "trigger_source": task.trigger_source,
                "attempt_count": task.attempt_count,
                "max_attempts": task.max_attempts,
                "last_error": task.last_error,
            }
            for task in tasks
        ]
        return ToolResult(
            tool_name="query_delayed_tasks",
            call_id="",
            ok=True,
            data={"tasks": rows},
        )
