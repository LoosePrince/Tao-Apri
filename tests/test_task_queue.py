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


def test_task_queue_retries_and_succeeds() -> None:
    queue = TaskQueue(enabled=True, worker_count=1, queue_size=10, max_retries=2)
    done = threading.Event()
    state = {"attempts": 0}

    def flaky_job() -> None:
        state["attempts"] += 1
        if state["attempts"] < 2:
            raise RuntimeError("temporary")
        done.set()

    queue.start()
    try:
        queue.submit(flaky_job)
        assert done.wait(timeout=2.0)
        assert state["attempts"] == 2
        assert queue.list_dead_letters() == []
    finally:
        queue.stop()


def test_task_queue_moves_failed_job_to_dead_letter_and_can_replay() -> None:
    queue = TaskQueue(enabled=True, worker_count=1, queue_size=10, max_retries=0)
    done = threading.Event()
    state = {"allow_success": False}

    def unstable_job() -> None:
        if not state["allow_success"]:
            raise RuntimeError("boom")
        done.set()

    queue.start()
    try:
        queue.submit(unstable_job)
        threading.Event().wait(0.2)
        dead = queue.list_dead_letters()
        assert len(dead) == 1
        assert dead[0].job_name == "unstable_job"
        state["allow_success"] = True
        replayed = queue.replay_dead_letters(limit=1)
        assert replayed == 1
        assert done.wait(timeout=2.0)
    finally:
        queue.stop()
