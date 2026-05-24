from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.pool import StaticPool
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Base, Job, JobAnalysis, JobRescoreRun, JobRescoreRunFailure, JobScore, JobSource, User, UserProfile
from app.db.session import get_db
from app.main import app
from app.services import rescore_runs


def test_stalled_runs_are_marked_from_missing_heartbeat() -> None:
    TestingSession = _session_factory()
    with TestingSession() as db:
        run = JobRescoreRun(
            status="running",
            total_jobs=4,
            completed_jobs=1,
            failed_jobs=0,
            last_heartbeat_at=datetime.now(tz=timezone.utc) - timedelta(minutes=20),
        )
        db.add(run)
        db.commit()
        run_id = run.id

        marked = rescore_runs.mark_stale_rescore_runs(db, heartbeat_seconds=60)

        stalled = db.get(JobRescoreRun, run_id)
        assert marked == 1
        assert stalled is not None
        assert stalled.status == "stalled"
        assert stalled.finished_at is not None


def test_failed_job_is_persisted_and_run_continues(monkeypatch) -> None:
    TestingSession = _session_factory()
    ids = _seed_rows(TestingSession)
    monkeypatch.setattr(rescore_runs, "SessionLocal", TestingSession)
    original_score = rescore_runs.score_job_against_profile

    def fail_one(db: Session, job: Job, user: User, profile: UserProfile):
        if job.source_job_id == "bad":
            raise ValueError("bad fixture job")
        return original_score(db, job, user, profile)

    monkeypatch.setattr(rescore_runs, "score_job_against_profile", fail_one)

    rescore_runs.run_rescore_background(ids["run"], ids["user"])

    with TestingSession() as db:
        run = db.get(JobRescoreRun, ids["run"])
        failures = db.scalars(select(JobRescoreRunFailure).where(JobRescoreRunFailure.run_id == ids["run"])).all()
        assert run is not None
        assert run.status == "completed"
        assert run.completed_jobs == 1
        assert run.failed_jobs == 1
        assert len(failures) == 1
        assert failures[0].job_id == ids["bad"]
        assert db.scalar(select(JobScore).where(JobScore.job_id == ids["good"])) is not None


def test_retry_stalled_run_creates_new_queued_run() -> None:
    TestingSession = _session_factory()
    ids = _seed_rows(TestingSession, status="stalled")
    with TestingSession() as db:
        user = db.get(User, ids["user"])
        assert user is not None
        started, created = rescore_runs.retry_rescore_run(db, ids["run"], user)
        assert created is True
        assert started is not None
        assert started.run_id != ids["run"]
        assert started.status == "queued"


def test_whole_run_timeout_marks_stalled(monkeypatch) -> None:
    TestingSession = _session_factory()
    ids = _seed_rows(TestingSession)
    monkeypatch.setattr(rescore_runs, "SessionLocal", TestingSession)
    monkeypatch.setattr(rescore_runs, "WHOLE_RUN_TIMEOUT_SECONDS", -1)

    rescore_runs.run_rescore_background(ids["run"], ids["user"])

    with TestingSession() as db:
        run = db.get(JobRescoreRun, ids["run"])
        assert run is not None
        assert run.status == "stalled"
        assert "timeout" in (run.error or "").lower()


def test_rescore_queue_completion_via_api(monkeypatch) -> None:
    TestingSession = _session_factory()
    _seed_rows(TestingSession, create_run=False)
    monkeypatch.setattr(rescore_runs, "SessionLocal", TestingSession)

    def override_get_db():
        with TestingSession() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        started = client.post("/jobs/rescore")
        assert started.status_code == 200
        run_id = started.json()["run_id"]
        status = client.get(f"/jobs/rescore-runs/{run_id}")
        assert status.status_code == 200
        body = status.json()
        assert body["status"] in {"queued", "running", "completed"}
        assert body["total_jobs"] >= body["completed_jobs"]
    finally:
        app.dependency_overrides.clear()


def _session_factory():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed_rows(TestingSession, *, status: str = "queued", create_run: bool = True) -> dict[str, int]:
    with TestingSession() as db:
        user = User(email="rescore@example.invalid")
        source = JobSource(name="Rescore Source", base_url="https://example.invalid", source_type="fixture")
        db.add_all([user, source])
        db.flush()
        profile = UserProfile(
            user_id=user.id,
            cv_text="Python SQL data engineering",
            summary="Data engineer",
            skills=["Python", "SQL"],
            experience=["Built Python SQL data pipelines"],
            projects=[],
            education=[],
            preferred_roles=["Data Engineer"],
            preferences={"remote": "remote", "target_seniority": "mid_senior"},
            remote_preference="remote",
            salary_min_preference=Decimal("40000"),
        )
        good = _job(db, source.id, "good")
        bad = _job(db, source.id, "bad")
        db.add(profile)
        run = None
        if create_run:
            run = JobRescoreRun(status=status, total_jobs=2, total=2, last_heartbeat_at=datetime.now(tz=timezone.utc))
            db.add(run)
        db.commit()
        return {"user": user.id, "run": run.id if run else 0, "good": good.id, "bad": bad.id}


def _job(db: Session, source_id: int, source_job_id: str) -> Job:
    job = Job(
        source_id=source_id,
        source_job_id=source_job_id,
        canonical_url=f"https://example.invalid/{source_job_id}",
        title="Data Engineer",
        company_name="Example Ltd",
        location="Remote UK",
        remote_type="remote",
        description_text="Build Python SQL data pipelines.",
        normalized_annual_min=Decimal("50000"),
    )
    db.add(job)
    db.flush()
    db.add(JobAnalysis(job_id=job.id, role_family="Data Engineer", requirements=["Python", "SQL"], responsibilities=["Build pipelines"]))
    return job
