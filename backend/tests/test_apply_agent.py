from collections.abc import Generator
from contextlib import contextmanager
from types import SimpleNamespace
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Job, JobSource, User, UserProfile
from app.db.session import get_db
from app.main import app
from app.schemas.database import AssistApplyResult
from app.services import apply_agent


def test_blocked_strategy_is_rejected(db_session) -> None:
    user, job = _seed_application(db_session)
    job.apply_strategy = "blocked"
    job.apply_difficulty = "blocked"
    db_session.commit()

    try:
        apply_agent.assist_apply_application(db_session, job, user, browser_runner=_fake_runner)
    except ValueError as exc:
        assert "Blocked apply routes" in str(exc)
    else:
        raise AssertionError("blocked strategy should be rejected")


def test_unavailable_job_is_rejected(db_session, monkeypatch) -> None:
    user, job = _seed_application(db_session)
    monkeypatch.setattr(
        apply_agent,
        "check_job_availability",
        lambda db, candidate: SimpleNamespace(availability_status="unavailable", availability_reason="HTTP 404"),
    )

    try:
        apply_agent.assist_apply_application(db_session, job, user, browser_runner=_fake_runner)
    except ValueError as exc:
        assert "job is unavailable" in str(exc)
    else:
        raise AssertionError("unavailable job should be rejected")


def test_missing_apply_url_is_rejected(db_session) -> None:
    user, job = _seed_application(db_session, url="")

    try:
        apply_agent.assist_apply_application(db_session, job, user, browser_runner=_fake_runner)
    except ValueError as exc:
        assert "Missing apply URL" in str(exc)
    else:
        raise AssertionError("missing apply URL should be rejected")


def test_safe_field_mapping_uses_exact_profile_values(db_session) -> None:
    user, _ = _seed_application(db_session)
    profile = UserProfile(
        user_id=user.id,
        cv_text="CV",
        preferences={
            "full_name": "Alex Applicant",
            "phone": "+44 7000 000000",
            "linkedin": "https://linkedin.com/in/alex",
            "work_authorization": "UK citizen, no sponsorship required",
        },
        location_preference="London",
    )
    db_session.add(profile)
    db_session.commit()

    candidates = apply_agent.profile_field_candidates(user, profile)

    assert candidates["email"].value == "apply-agent@example.invalid"
    assert candidates["name"].value == "Alex Applicant"
    assert apply_agent.classify_form_field("Email address") == "email"
    assert apply_agent.classify_form_field("Visa sponsorship required?") == "work_authorization"
    assert apply_agent.classify_form_field("First name") == "first_name"


def test_assist_apply_endpoint_never_submits(monkeypatch) -> None:
    submitted = False

    def fake_runner(url, candidates, profile, mode, apply_strategy):
        nonlocal submitted
        assert mode == "review_only"
        submitted = False
        return AssistApplyResult(
            status="review_required",
            filled_fields=["Email"],
            unfilled_fields=["First name"],
            unfilled_required_fields=[],
            uploaded_cv=False,
            submitted=False,
            warnings=["Submit control detected and intentionally not clicked."],
            screenshot_path=None,
        )

    monkeypatch.setattr(
        apply_agent,
        "check_job_availability",
        lambda db, candidate: SimpleNamespace(availability_status="active", availability_reason="fixture"),
    )
    monkeypatch.setattr(apply_agent, "run_playwright_assist", fake_runner)
    with apply_client() as (client, ids):
        response = client.post(f"/applications/{ids['job']}/assist-apply")

    assert response.status_code == 200
    assert response.json()["status"] == "review_required"
    assert submitted is False
    assert any("intentionally not clicked" in warning for warning in response.json()["warnings"])


def test_submit_requires_explicit_mode(monkeypatch) -> None:
    seen_modes = []

    def fake_runner(url, candidates, profile, mode, apply_strategy):
        seen_modes.append(mode)
        return AssistApplyResult(status="review_required", filled_fields=[], unfilled_fields=[], warnings=[], screenshot_path=None)

    monkeypatch.setattr(apply_agent, "check_job_availability", lambda db, candidate: SimpleNamespace(availability_status="active", availability_reason="fixture"))
    monkeypatch.setattr(apply_agent, "run_playwright_assist", fake_runner)
    with apply_client(jobserve=True, with_profile=True) as (client, ids):
        response = client.post(f"/applications/{ids['job']}/assist-apply", json={"mode": "review_only"})

    assert response.status_code == 200
    assert seen_modes == ["review_only"]


def test_missing_cv_blocks_submit(monkeypatch) -> None:
    monkeypatch.setattr(apply_agent, "check_job_availability", lambda db, candidate: SimpleNamespace(availability_status="active", availability_reason="fixture"))
    with apply_client(jobserve=True, with_profile=True, with_cv=False) as (client, ids):
        response = client.post(f"/applications/{ids['job']}/assist-apply", json={"mode": "submit_with_confirmation"})

    assert response.status_code == 400
    assert "Saved CV file is required" in response.json()["detail"]


def test_missing_email_blocks_submit(monkeypatch) -> None:
    monkeypatch.setattr(apply_agent, "check_job_availability", lambda db, candidate: SimpleNamespace(availability_status="active", availability_reason="fixture"))
    with apply_client(jobserve=True, with_profile=True, with_cv=True, email="") as (client, ids):
        response = client.post(f"/applications/{ids['job']}/assist-apply", json={"mode": "submit_with_confirmation"})

    assert response.status_code == 400
    assert "Email is required" in response.json()["detail"]


def test_unavailable_job_blocks_submit(monkeypatch) -> None:
    monkeypatch.setattr(apply_agent, "check_job_availability", lambda db, candidate: SimpleNamespace(availability_status="expired", availability_reason="closed"))
    with apply_client(jobserve=True, with_profile=True, with_cv=True) as (client, ids):
        response = client.post(f"/applications/{ids['job']}/assist-apply", json={"mode": "submit_with_confirmation"})

    assert response.status_code == 400
    assert "job is expired" in response.json()["detail"]


def test_successful_jobserve_submit_marks_applied(monkeypatch) -> None:
    def fake_runner(url, candidates, profile, mode, apply_strategy):
        assert mode == "submit_with_confirmation"
        assert apply_strategy == "jobserve_apply_easy"
        return AssistApplyResult(
            status="submitted",
            filled_fields=["Email Address", "CV upload"],
            unfilled_fields=[],
            unfilled_required_fields=[],
            uploaded_cv=True,
            submitted=True,
            warnings=["Disabled option: register a Job Seeker account."],
            screenshot_path=None,
        )

    monkeypatch.setattr(apply_agent, "check_job_availability", lambda db, candidate: SimpleNamespace(availability_status="active", availability_reason="fixture"))
    monkeypatch.setattr(apply_agent, "run_playwright_assist", fake_runner)
    with apply_client(jobserve=True, with_profile=True, with_cv=True) as (client, ids):
        response = client.post(f"/applications/{ids['job']}/assist-apply", json={"mode": "submit_with_confirmation"})
        with ids["Session"]() as db:
            job = db.get(Job, ids["job"])

    assert response.status_code == 200
    assert response.json()["submitted"] is True
    assert job.application_status == "applied"
    assert job.applied_at is not None


def test_account_registration_toggles_are_disabled_if_present() -> None:
    warnings = []
    controls = [_FakeControl(True), _FakeControl(False)]
    apply_agent._disable_jobserve_account_options(_FakePage(controls), warnings)

    assert controls[0].unchecked is True
    assert controls[1].unchecked is False
    assert any("Disabled option" in warning for warning in warnings)


@contextmanager
def apply_client(
    *,
    jobserve: bool = False,
    with_profile: bool = False,
    with_cv: bool = False,
    email: str = "apply-agent@example.invalid",
) -> Generator[tuple[TestClient, dict], None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)
    with TestingSession() as db:
        user, job = _seed_application(db, jobserve=jobserve)
        if with_profile:
            cv_path = str(Path(__file__).resolve()) if with_cv else None
            db.add(
                UserProfile(
                    user_id=user.id,
                    cv_text="CV",
                    email=email,
                    first_name="Alex",
                    last_name="Applicant",
                    phone="07000000000",
                    work_status_uk="UK citizen",
                    cv_file_path=cv_path,
                    cv_file_name="cv.pdf" if cv_path else None,
                )
            )
            db.commit()
        ids = {"user": user.id, "job": job.id, "Session": TestingSession}

    def override_get_db():
        with TestingSession() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app), ids
    finally:
        app.dependency_overrides.clear()


def _seed_application(db_session, *, url: str = "https://example.invalid/apply", jobserve: bool = False) -> tuple[User, Job]:
    user = User(email="apply-agent@example.invalid")
    source = JobSource(name=f"Apply Agent Source {id(db_session)}", base_url="https://example.invalid", source_type="fixture")
    db_session.add_all([user, source])
    db_session.flush()
    job = Job(
        source_id=source.id,
        source_job_id=f"job-{id(user)}",
        canonical_url=url,
        title="AI Engineer",
        company_name="Example Ltd",
        application_status="ready_to_apply",
        availability_status="active",
        apply_strategy="jobserve_apply_easy" if jobserve else "greenhouse",
        apply_difficulty="easy" if jobserve else "medium",
    )
    db_session.add(job)
    db_session.commit()
    return user, job


def _fake_runner(url, candidates, profile, mode, apply_strategy):
    return AssistApplyResult(status="review_required", filled_fields=["Email"], unfilled_fields=[], warnings=[], screenshot_path=None)


class _FakeControl:
    def __init__(self, checked: bool) -> None:
        self.checked = checked
        self.unchecked = False

    def is_checked(self):
        return self.checked

    def uncheck(self):
        self.unchecked = True


class _FakeLocator:
    def __init__(self, controls) -> None:
        self.controls = controls

    def all(self):
        return self.controls


class _FakePage:
    def __init__(self, controls) -> None:
        self.controls = controls

    def get_by_label(self, pattern):
        return _FakeLocator(self.controls)
