import threading

from app.jobs.task_queue import TaskQueue


def test_task_queue_runs_inline_when_disabled() -> None:
    queue = TaskQueue(enabled=False, worker_count=1, queue_size=10)
    state = {"value": 0}

    def job() -> None:
        state["value"] = 1

    queue.submit(job)
    assert state["value"] == 1


def test_task_queue_executes_background_job_when_enabled() -> None:
    queue = TaskQueue(enabled=True, worker_count=1, queue_size=10)
    state = {"value": 0}
    done = threading.Event()

    def job() -> None:
        state["value"] = 2
        done.set()

    queue.start()
    try:
        queue.submit(job)
        assert done.wait(timeout=2.0)
        assert state["value"] == 2
    finally:
        queue.stop()
