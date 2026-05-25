from collections.abc import Generator
from contextlib import contextmanager
from types import SimpleNamespace

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
    assert apply_agent.classify_form_field("First name") is None


def test_assist_apply_endpoint_never_submits(monkeypatch) -> None:
    submitted = False

    def fake_runner(url, candidates):
        nonlocal submitted
        submitted = False
        return AssistApplyResult(
            status="review_required",
            filled_fields=["Email"],
            unfilled_fields=["First name"],
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


@contextmanager
def apply_client() -> Generator[tuple[TestClient, dict[str, int]], None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)
    with TestingSession() as db:
        user, job = _seed_application(db)
        ids = {"user": user.id, "job": job.id}

    def override_get_db():
        with TestingSession() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app), ids
    finally:
        app.dependency_overrides.clear()


def _seed_application(db_session, *, url: str = "https://example.invalid/apply") -> tuple[User, Job]:
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
        apply_strategy="greenhouse",
        apply_difficulty="medium",
    )
    db_session.add(job)
    db_session.commit()
    return user, job


def _fake_runner(url, candidates):
    return AssistApplyResult(status="review_required", filled_fields=["Email"], unfilled_fields=[], warnings=[], screenshot_path=None)
