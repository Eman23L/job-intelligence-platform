from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import ApplicationPrepareRun, Base, JobRescoreRun, ScrapeRun, User
from app.db.session import get_db
from app.main import app
from app.services import queue


@contextmanager
def runs_client() -> Generator[tuple[TestClient, sessionmaker, dict[str, int]], None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)
    with TestingSession() as db:
        db.add(User(email="runs@example.invalid"))
        scrape = ScrapeRun(source_id=_source_id(db), status="completed", jobs_found=2, jobs_created=1, jobs_updated=1, jobs_skipped=0)
        rescore = JobRescoreRun(
            status="running",
            total_jobs=10,
            completed_jobs=2,
            failed_jobs=1,
            last_heartbeat_at=datetime.now(tz=timezone.utc),
        )
        stale = JobRescoreRun(
            status="running",
            total_jobs=5,
            completed_jobs=1,
            last_heartbeat_at=datetime.now(tz=timezone.utc) - timedelta(minutes=10),
        )
        prepare = ApplicationPrepareRun(status="running", total=3, processed=1, queued=1, last_heartbeat_at=datetime.now(tz=timezone.utc))
        db.add_all([scrape, rescore, stale, prepare])
        db.commit()
        ids = {"scrape": scrape.id, "rescore": rescore.id, "stale": stale.id, "prepare": prepare.id}

    def override_get_db():
        with TestingSession() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app), TestingSession, ids
    finally:
        app.dependency_overrides.clear()


def test_runs_endpoint_returns_unified_runs() -> None:
    with runs_client() as (client, _, _):
        response = client.get("/runs?type=all&status=all&limit=20")

        assert response.status_code == 200
        items = response.json()["items"]
        assert {"scrape", "rescore", "application_prepare"}.issubset({item["type"] for item in items})
        assert all("duration_seconds" in item for item in items)


def test_runs_endpoint_projects_stalled_status() -> None:
    with runs_client() as (client, _, ids):
        response = client.get("/runs?type=rescore&status=stalled")

        assert response.status_code == 200
        items = response.json()["items"]
        assert [item["id"] for item in items] == [str(ids["stale"])]
        assert items[0]["status"] == "stalled"


def test_runs_cancel_marks_active_run_canceled() -> None:
    with runs_client() as (client, TestingSession, ids):
        response = client.post(f"/runs/application_prepare/{ids['prepare']}/cancel")

        assert response.status_code == 200
        assert response.json()["status"] == "canceled"
        with TestingSession() as db:
            assert db.get(ApplicationPrepareRun, ids["prepare"]).status == "canceled"


def test_runs_retry_enqueues_safe_run(monkeypatch) -> None:
    enqueued = []
    monkeypatch.setattr(queue, "queue_enabled", lambda: True)
    monkeypatch.setattr(queue, "rq_queue", lambda: _FakeQueue(enqueued))
    with runs_client() as (client, TestingSession, ids):
        with TestingSession() as db:
            active_run = db.get(JobRescoreRun, ids["rescore"])
            active_run.status = "completed"
            active_run.finished_at = datetime.now(timezone.utc)
            run = db.get(JobRescoreRun, ids["stale"])
            run.status = "stalled"
            db.commit()

        response = client.post(f"/runs/rescore/{ids['stale']}/retry")

        assert response.status_code == 200
        assert response.json()["type"] == "rescore"
        assert enqueued


def _source_id(db) -> int:
    from app.db.models import JobSource

    source = JobSource(name="Runs Source", base_url="https://example.invalid", source_type="fixture")
    db.add(source)
    db.flush()
    return source.id


class _FakeJob:
    id = "fake-job"


class _FakeQueue:
    def __init__(self, calls):
        self.calls = calls

    def enqueue_call(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeJob()
