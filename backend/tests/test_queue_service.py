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
    assert call["timeout"] == queue.settings.scrape_timeout_seconds
    assert call["result_ttl"] == 3600
    assert call["failure_ttl"] == 86400


def test_enqueue_uses_apply_timeout_for_browser_jobs(monkeypatch) -> None:
    fake_queue = FakeQueue()
    monkeypatch.setattr(queue, "queue_enabled", lambda: True)
    monkeypatch.setattr(queue, "rq_queue", lambda: fake_queue)

    from app.services.apply_agent import run_assist_apply_background

    queue.enqueue_or_background(BackgroundTasks(), run_assist_apply_background, 1, 1, job_id="apply")

    assert fake_queue.calls[0]["timeout"] == queue.settings.apply_timeout_seconds


def test_enqueue_accepts_explicit_timeout_and_ttls(monkeypatch) -> None:
    fake_queue = FakeQueue()
    monkeypatch.setattr(queue, "queue_enabled", lambda: True)
    monkeypatch.setattr(queue, "rq_queue", lambda: fake_queue)

    queue.enqueue_or_background(BackgroundTasks(), sample_task, 41, job_timeout=900, result_ttl=7200, failure_ttl=90000)

    call = fake_queue.calls[0]
    assert call["timeout"] == 900
    assert call["result_ttl"] == 7200
    assert call["failure_ttl"] == 90000


def test_enqueue_falls_back_to_background_tasks(monkeypatch) -> None:
    tasks = BackgroundTasks()
    monkeypatch.setattr(queue, "queue_enabled", lambda: False)

    job_id = queue.enqueue_or_background(tasks, sample_task, 41)

    assert job_id == "background"
    assert len(tasks.tasks) == 1


def test_call_by_path_invokes_worker_function() -> None:
    assert queue.call_by_path(f"{__name__}.sample_task", 41) == 42
