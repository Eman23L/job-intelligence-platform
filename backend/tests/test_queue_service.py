from fastapi import BackgroundTasks

from app.services import queue


def sample_task(value: int) -> int:
    return value + 1


class FakeJob:
    id = "fake-rq-job"


class FakeQueue:
    def __init__(self) -> None:
        self.calls = []

    def enqueue_call(self, **kwargs):
        self.calls.append(kwargs)
        return FakeJob()


def test_enqueue_uses_rq_when_enabled(monkeypatch) -> None:
    fake_queue = FakeQueue()
    monkeypatch.setattr(queue, "queue_enabled", lambda: True)
    monkeypatch.setattr(queue, "rq_queue", lambda: fake_queue)

    job_id = queue.enqueue_or_background(BackgroundTasks(), sample_task, 41, job_id="sample")

    assert job_id == "fake-rq-job"
    assert fake_queue.calls
    call = fake_queue.calls[0]
    assert call["args"][0].endswith("sample_task")
    assert call["args"][1] == 41
    assert call["job_id"] == "sample"


def test_enqueue_falls_back_to_background_tasks(monkeypatch) -> None:
    tasks = BackgroundTasks()
    monkeypatch.setattr(queue, "queue_enabled", lambda: False)

    job_id = queue.enqueue_or_background(tasks, sample_task, 41)

    assert job_id == "background"
    assert len(tasks.tasks) == 1


def test_call_by_path_invokes_worker_function() -> None:
    assert queue.call_by_path(f"{__name__}.sample_task", 41) == 42
