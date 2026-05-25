from datetime import datetime, timezone
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import ApplicationPrepareRun, Base, Job, JobScore, JobSource, User
from app.db.session import get_db
from app.main import app
from app.services import applications
from app.services import queue


def test_prepare_returns_run_id_quickly_and_queues_after_completion(monkeypatch) -> None:
    TestingSession = _session_factory()
    ids = _seed_jobs(TestingSession, count=2)
    monkeypatch.setattr(applications, "SessionLocal", TestingSession)
    monkeypatch.setattr(applications, "check_job_availability", _mark_active)

    def override_get_db():
        with TestingSession() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        started = client.post("/applications/prepare")
        assert started.status_code == 200
        assert started.json()["run_id"]
        status = client.get(f"/applications/prepare-runs/{started.json()['run_id']}")
        assert status.status_code == 200
        body = status.json()
        assert body["status"] == "completed"
        assert body["processed"] == 2
        assert body["queued"] == 2
        listed = client.get("/applications")
        assert {item["job_id"] for item in listed.json()["items"]} == set(ids["jobs"])
    finally:
        app.dependency_overrides.clear()


def test_prepare_api_enqueues_when_queue_enabled(monkeypatch) -> None:
    TestingSession = _session_factory()
    _seed_jobs(TestingSession, count=1)
    enqueued = []
    monkeypatch.setattr(queue, "queue_enabled", lambda: True)
    monkeypatch.setattr(queue, "rq_queue", lambda: _FakeQueue(enqueued))

    def override_get_db():
        with TestingSession() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = TestClient(app).post("/applications/prepare")
        assert response.status_code == 200
        assert response.json()["run_id"]
        assert enqueued
    finally:
        app.dependency_overrides.clear()


def test_prepare_failed_job_does_not_break_whole_run(monkeypatch) -> None:
    TestingSession = _session_factory()
    ids = _seed_jobs(TestingSession, count=2)
    monkeypatch.setattr(applications, "SessionLocal", TestingSession)

    def fail_one(db: Session, job: Job) -> None:
        if job.id == ids["jobs"][0]:
            raise ValueError("availability failure")
        _mark_active(db, job)

    monkeypatch.setattr(applications, "check_job_availability", fail_one)

    with TestingSession() as db:
        user = db.get(User, ids["user"])
        assert user is not None
        started, _ = applications.start_prepare_applications_run(db, user)

    applications.run_prepare_applications_background(started.run_id, ids["user"])

    with TestingSession() as db:
        run = db.get(ApplicationPrepareRun, started.run_id)
        assert run is not None
        assert run.status == "completed"
        assert run.processed == 2
        assert run.failed == 1
        assert run.queued == 1


def test_prepare_duplicate_jobs_are_not_requeued(monkeypatch) -> None:
    TestingSession = _session_factory()
    ids = _seed_jobs(TestingSession, count=1)
    monkeypatch.setattr(applications, "SessionLocal", TestingSession)
    monkeypatch.setattr(applications, "check_job_availability", _mark_active)

    with TestingSession() as db:
        user = db.get(User, ids["user"])
        assert user is not None
        first, _ = applications.start_prepare_applications_run(db, user)
    applications.run_prepare_applications_background(first.run_id, ids["user"])

    with TestingSession() as db:
        user = db.get(User, ids["user"])
        assert user is not None
        second, _ = applications.start_prepare_applications_run(db, user)
    applications.run_prepare_applications_background(second.run_id, ids["user"])

    with TestingSession() as db:
        run = db.get(ApplicationPrepareRun, second.run_id)
        assert run is not None
        assert run.status == "completed"
        assert run.queued == 0
        assert run.skipped == 1
        assert db.get(Job, ids["jobs"][0]).application_status == "ready_to_apply"


def _session_factory():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed_jobs(TestingSession, *, count: int) -> dict[str, int | list[int]]:
    with TestingSession() as db:
        user = User(email="prepare@example.invalid")
        source = JobSource(name="Prepare Source", base_url="https://example.invalid", source_type="fixture")
        db.add_all([user, source])
        db.flush()
        job_ids = []
        for index in range(count):
            job = Job(
                source_id=source.id,
                source_job_id=f"job-{index}",
                canonical_url=f"https://example.invalid/jobs/{index}",
                title=f"Data Engineer {index}",
                company_name="Example Ltd",
                application_status="not_started",
                availability_status="unknown",
            )
            db.add(job)
            db.flush()
            db.add(JobScore(job_id=job.id, user_id=user.id, total_score=Decimal("85"), recommendation="apply", recommendation_tier="Strong match"))
            job_ids.append(job.id)
        db.commit()
        return {"user": user.id, "jobs": job_ids}


def _mark_active(db: Session, job: Job) -> None:
    job.availability_status = "active"
    job.last_checked_at = datetime.now(tz=timezone.utc)
    job.availability_reason = "fixture"
    db.commit()


class _FakeJob:
    id = "fake-job"


class _FakeQueue:
    def __init__(self, calls):
        self.calls = calls

    def enqueue_call(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeJob()
