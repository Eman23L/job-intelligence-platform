from collections.abc import Generator
from contextlib import contextmanager
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Job, JobAnalysis, JobScore, JobSkill, JobSource, MissingSkill, User, UserProfile
from app.db.session import get_db
from app.main import app
from app.services.job_scoring import build_scorecard, rescore_jobs, score_job_against_profile, score_tier


@contextmanager
def scoring_client() -> Generator[tuple[TestClient, sessionmaker[Session], dict[str, int]], None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)
    with TestingSession() as db:
        ids = _seed_scoring_data(db)

    def override_get_db():
        with TestingSession() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app), TestingSession, ids
    finally:
        app.dependency_overrides.clear()


def test_high_skill_overlap_scores_higher_than_low_overlap(db_session: Session) -> None:
    user, profile, jobs = _seed_scoring_rows(db_session)

    high = build_scorecard(db_session, jobs["high"], profile)
    low = build_scorecard(db_session, jobs["low"], profile)

    assert high.total_score > low.total_score
    assert high.recommendation == "apply"
    assert "Python" in high.matched_skills


def test_senior_leadership_role_is_penalised_by_default(db_session: Session) -> None:
    _, profile, jobs = _seed_scoring_rows(db_session)
    source_id = jobs["high"].source_id
    senior = _job(
        db_session,
        source_id,
        "senior-manager",
        "Senior Data Engineering Manager",
        "Remote UK",
        "remote",
        "Lead Python SQL data platform delivery and manage engineering teams.",
        ["Python", "SQL", "Power BI"],
    )

    result = build_scorecard(db_session, senior, profile)

    assert result.total_score < build_scorecard(db_session, jobs["high"], profile).total_score
    assert "Penalised because role appears too senior." in result.risks
    assert result.recommendation in {"maybe", "skip"}


def test_senior_preference_allows_senior_technical_role(db_session: Session) -> None:
    _, profile, jobs = _seed_scoring_rows(db_session)
    profile.preferences["target_seniority"] = "senior"
    source_id = jobs["high"].source_id
    senior = _job(
        db_session,
        source_id,
        "senior-engineer",
        "Senior Data Engineer",
        "Remote UK",
        "remote",
        "Build Python SQL data pipelines and Power BI dashboards.",
        ["Python", "SQL", "Power BI"],
    )

    result = build_scorecard(db_session, senior, profile)

    assert "Penalised because role appears too senior." not in result.risks
    assert result.total_score >= 70


def test_mid_level_technical_growth_role_gets_growth_evidence(db_session: Session) -> None:
    _, profile, jobs = _seed_scoring_rows(db_session)

    result = build_scorecard(db_session, jobs["high"], profile)

    assert "Good growth role." in result.evidence
    assert "Suitable mid-level technical role." in result.evidence


def test_missing_required_skills_lowers_score_and_records_missing(db_session: Session) -> None:
    user, profile, jobs = _seed_scoring_rows(db_session)

    score_job_against_profile(db_session, jobs["low"], user, profile)
    db_session.commit()

    stored = db_session.scalar(select(JobScore).where(JobScore.job_id == jobs["low"].id))
    missing = db_session.scalars(select(MissingSkill).where(MissingSkill.job_id == jobs["low"].id)).all()
    assert stored is not None
    assert stored.total_score < Decimal("70")
    assert {skill.skill_name for skill in missing} >= {"Kubernetes", "Scala"}


def test_sponsorship_mismatch_triggers_gate_and_risk(db_session: Session) -> None:
    _, profile, jobs = _seed_scoring_rows(db_session, work_authorization="")

    result = build_scorecard(db_session, jobs["sponsorship"], profile)

    assert "work_authorization" in result.gates
    assert result.recommendation == "skip"
    assert result.total_score <= 49
    assert any("sponsorship" in risk.lower() for risk in result.risks)


def test_excluded_jobs_are_ignored_by_rescore(db_session: Session) -> None:
    _, _, jobs = _seed_scoring_rows(db_session)

    result = rescore_jobs(db_session)

    assert result == {"jobs_scored": 3, "jobs_skipped": 1}
    excluded_score = db_session.scalar(select(JobScore).where(JobScore.job_id == jobs["excluded"].id))
    assert excluded_score is None


def test_score_tiers_are_mvp_thresholds() -> None:
    assert score_tier(85) == "Excellent match"
    assert score_tier(70) == "Strong match"
    assert score_tier(50) == "Possible match"
    assert score_tier(49) == "Weak match"


def test_scorecard_endpoint_returns_explainable_breakdown() -> None:
    with scoring_client() as (client, TestingSession, ids):
        with TestingSession() as db:
            user = db.scalar(select(User).where(User.id == ids["user"]))
            profile = db.scalar(select(UserProfile).where(UserProfile.user_id == ids["user"]))
            job = db.scalar(select(Job).where(Job.id == ids["high"]))
            assert user is not None and profile is not None and job is not None
            score_job_against_profile(db, job, user, profile)
            db.commit()

        response = client.get(f"/jobs/{ids['high']}/scorecard")

        assert response.status_code == 200
        body = response.json()
        assert body["job_id"] == ids["high"]
        assert set(body["score_breakdown"]) == {
            "skill_match",
            "experience_relevance",
            "role_family_fit",
            "location_remote_fit",
            "salary_fit",
            "confidence",
        }
        assert body["matched_skills"]
        assert body["matched_evidence"]
        assert body["recommendation"] in {"apply", "maybe", "skip"}


def _seed_scoring_data(db: Session) -> dict[str, int]:
    user, _, jobs = _seed_scoring_rows(db)
    return {"user": user.id, **{name: job.id for name, job in jobs.items()}}


def _seed_scoring_rows(
    db: Session,
    *,
    work_authorization: str = "does not require sponsorship; SC Cleared; BPSS Cleared",
) -> tuple[User, UserProfile, dict[str, Job]]:
    source = JobSource(name="Test Source", base_url="https://example.com", source_type="test")
    user = User(email="scoring@example.com")
    db.add_all([source, user])
    db.flush()
    profile = UserProfile(
        user_id=user.id,
        cv_text="Python SQL React data pipelines and reporting automation.",
        summary="Data engineer with reporting automation experience.",
        skills=["Python", "SQL", "React", "Power BI"],
        experience=["Built Python SQL data pipelines and dashboard reporting automation."],
        projects=["Reporting automation platform with React and Power BI."],
        education=["University of Roehampton"],
        preferred_roles=["Data Engineer"],
        preferences={
            "remote": "remote",
            "location": "Remote UK",
            "salary": "50000-70000",
            "work_authorization": work_authorization,
            "target_seniority": "mid_senior",
        },
        remote_preference="remote",
        location_preference="Remote UK",
        salary_min_preference=Decimal("50000"),
        salary_max_preference=Decimal("70000"),
    )
    db.add(profile)
    db.flush()

    high = _job(
        db,
        source.id,
        "high",
        "Data Engineer",
        "Remote UK",
        "remote",
        "Build Python SQL data pipelines, React reporting tools, and Power BI dashboards.",
        ["Python", "SQL", "React", "Power BI"],
    )
    low = _job(
        db,
        source.id,
        "low",
        "Platform Engineer",
        "Remote UK",
        "remote",
        "Build Scala services on Kubernetes with Terraform.",
        ["Scala", "Kubernetes", "Terraform"],
    )
    sponsorship = _job(
        db,
        source.id,
        "sponsorship",
        "Data Engineer",
        "Remote UK",
        "remote",
        "Build Python pipelines. Visa sponsorship is required for this role.",
        ["Python", "SQL"],
    )
    excluded = _job(
        db,
        source.id,
        "excluded",
        "Excluded Data Engineer",
        "Remote UK",
        "remote",
        "Build Python pipelines.",
        ["Python"],
        status="excluded",
    )
    db.commit()
    return user, profile, {"high": high, "low": low, "sponsorship": sponsorship, "excluded": excluded}


def _job(
    db: Session,
    source_id: int,
    source_job_id: str,
    title: str,
    location: str,
    remote_type: str,
    description: str,
    skills: list[str],
    *,
    status: str = "active",
) -> Job:
    job = Job(
        source_id=source_id,
        source_job_id=source_job_id,
        canonical_url=f"https://example.com/{source_job_id}",
        title=title,
        company_name="Example Ltd",
        location=location,
        remote_type=remote_type,
        salary_currency="GBP",
        normalized_annual_min=Decimal("55000"),
        normalized_annual_max=Decimal("75000"),
        description_text=description,
        status=status,
    )
    db.add(job)
    db.flush()
    db.add(
        JobAnalysis(
            job_id=job.id,
            role_family="Data Engineer" if "Data" in title else "Platform Engineer",
            tools_detected=[],
            requirements=skills,
            responsibilities=[description],
        )
    )
    db.add_all([JobSkill(job_id=job.id, skill_name=skill, importance="required") for skill in skills])
    db.flush()
    return job
