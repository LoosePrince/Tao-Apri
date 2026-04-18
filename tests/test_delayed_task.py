from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import time

from app.core.config import settings
from app.domain.conversation_scope import ConversationScope
from app.domain.models import DelayedTask
from app.jobs.delayed_task_scheduler import DelayedTaskScheduler
from app.jobs.task_queue import TaskQueue
from app.repos.in_memory import InMemoryDelayedTaskRepo
from app.repos.sqlite_repo import SQLiteDelayedTaskRepo, SQLiteStore
from app.tool_runtime.builtin_tools import ScheduleDelayedTaskTool
from app.tool_runtime.builtin_tools import CancelDelayedTaskTool, QueryDelayedTasksTool


def test_sqlite_delayed_task_repo_claim_and_mark_done():
    with TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "test.db"
        store = SQLiteStore(str(db_path))
        try:
            repo = SQLiteDelayedTaskRepo(store)
            task = DelayedTask(
                task_id="task-1",
                run_at=datetime.now(UTC) - timedelta(seconds=5),
                description="desc",
                reason="reason",
                trigger_source="test",
                payload_json="{}",
                max_attempts=3,
            )
            repo.enqueue(task)
            claimed = repo.claim_due(
                now_iso=datetime.now(UTC).isoformat(),
                limit=5,
                worker_id="w1",
            )
            assert len(claimed) == 1
            assert claimed[0].status == "running"
            repo.mark_done("task-1")
            done = repo.get("task-1")
            assert done is not None
            assert done.status == "done"
        finally:
            store.conn.close()


def test_schedule_delayed_task_tool_accepts_delay_seconds():
    repo = InMemoryDelayedTaskRepo()
    tool = ScheduleDelayedTaskTool(
        delayed_task_repo=repo,
        viewer_scope=ConversationScope.private(platform="test", user_id="u-1"),
    )
    result = tool.call(
        {
            "time": "30s",
            "description": "30秒后提醒",
            "reason": "测试定时",
            "trigger_source": "unit-test",
            "task_payload": {"message": "执行定时任务"},
        }
    )
    assert result.ok is True
    task_id = result.data["task_id"]
    created = repo.get(task_id)
    assert created is not None
    assert created.description == "30秒后提醒"
    assert created.trigger_source == "unit-test"


def test_schedule_delayed_task_tool_accepts_absolute_time_format():
    repo = InMemoryDelayedTaskRepo()
    tool = ScheduleDelayedTaskTool(
        delayed_task_repo=repo,
        viewer_scope=ConversationScope.private(platform="test", user_id="u-1"),
    )
    result = tool.call(
        {
            "time": "2026.4.18 17:23:59",
            "description": "绝对时间提醒",
            "reason": "测试绝对时间",
            "trigger_source": "unit-test",
        }
    )
    assert result.ok is True


def test_schedule_delayed_task_tool_rejects_invalid_time_format():
    repo = InMemoryDelayedTaskRepo()
    tool = ScheduleDelayedTaskTool(
        delayed_task_repo=repo,
        viewer_scope=ConversationScope.private(platform="test", user_id="u-1"),
    )
    result = tool.call(
        {
            "time": "2026-04-18T17:23:59+08:00",
            "description": "无效格式",
            "reason": "测试无效格式",
            "trigger_source": "unit-test",
        }
    )
    assert result.ok is False
    assert "invalid time format" in result.error


def test_delayed_task_scheduler_executes_due_task():
    repo = InMemoryDelayedTaskRepo()
    task = DelayedTask(
        task_id="task-due",
        run_at=datetime.now(UTC) - timedelta(seconds=1),
        description="execute",
        reason="test",
        trigger_source="test",
        payload_json="{}",
    )
    repo.enqueue(task)
    executed: list[str] = []

    original_enabled = settings.delayed_task.enabled
    original_poll = settings.delayed_task.poll_interval_seconds
    original_batch = settings.delayed_task.claim_batch_size
    try:
        settings.delayed_task.enabled = True
        settings.delayed_task.poll_interval_seconds = 0.2
        settings.delayed_task.claim_batch_size = 5
        scheduler = DelayedTaskScheduler(
            repo=repo,
            task_queue=TaskQueue(enabled=False, worker_count=1, queue_size=10),
            executor=lambda delayed_task: executed.append(delayed_task.task_id),
        )
        scheduler.start()
        time.sleep(0.5)
        scheduler.stop()
    finally:
        settings.delayed_task.enabled = original_enabled
        settings.delayed_task.poll_interval_seconds = original_poll
        settings.delayed_task.claim_batch_size = original_batch

    assert "task-due" in executed
    done = repo.get("task-due")
    assert done is not None
    assert done.status == "done"


def test_query_and_cancel_delayed_task_tools():
    repo = InMemoryDelayedTaskRepo()
    now = datetime.now(UTC)
    task = DelayedTask(
        task_id="task-query-1",
        run_at=now + timedelta(minutes=5),
        status="pending",
        description="query-test",
        reason="test",
        trigger_source="unit",
        payload_json="{}",
        scope_id="private:u-1",
    )
    repo.enqueue(task)
    scope = ConversationScope.private(platform="test", user_id="u-1")
    query_tool = QueryDelayedTasksTool(delayed_task_repo=repo, viewer_scope=scope)
    query_result = query_tool.call({"status": "pending", "limit": 10})
    assert query_result.ok is True
    assert query_result.data["tasks"]
    assert query_result.data["tasks"][0]["task_id"] == "task-query-1"

    cancel_tool = CancelDelayedTaskTool(delayed_task_repo=repo, viewer_scope=scope)
    cancel_result = cancel_tool.call({"task_id": "task-query-1"})
    assert cancel_result.ok is True
    cancelled = repo.get("task-query-1")
    assert cancelled is not None
    assert cancelled.status == "cancelled"
