from datetime import datetime, timezone
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Job, JobAnalysis, JobScore, JobSkill, JobSource, MissingSkill, TargetRole, UserSkill
from app.db.session import get_db
from app.main import app
from app.services.scoring import (
    generate_explanation,
    recommendation_tier,
    score_job,
    score_role_match,
    score_skill_match,
)
from scripts.seed_data import seed_database


def test_role_match_scoring(db_session) -> None:
    user = seed_database(db_session)
    targets = db_session.scalars(select(TargetRole).where(TargetRole.user_id == user.id)).all()

    assert score_role_match("Data Engineer", targets) == 25
    assert score_role_match("Software Engineer", targets) == 15
    assert score_role_match("Marketing Manager", targets) == 5


def test_skill_match_scoring(db_session) -> None:
    user = seed_database(db_session)
    user_skills = db_session.scalars(select(UserSkill).where(UserSkill.user_id == user.id)).all()
    job_skills = [
        JobSkill(job_id=1, skill_name="Python", importance="essential"),
        JobSkill(job_id=1, skill_name="SQL", importance="essential"),
        JobSkill(job_id=1, skill_name="Databricks", importance="nice_to_have"),
    ]

    result = score_skill_match(job_skills, user_skills)

    assert result.matched_skills == ["Python", "SQL"]
    assert [skill.skill_name for skill in result.nice_to_have_missing] == ["Databricks"]
    assert result.score > 25


def test_missing_skill_creation(db_session) -> None:
    user = seed_database(db_session)
    job = _create_scored_fixture_job(
        db_session,
        "Strong Match",
        "Data Engineer",
        ["Python", "SQL", "Databricks"],
        ["essential", "essential", "essential"],
    )

    score = score_job(db_session, job, user)
    missing = db_session.scalars(select(MissingSkill).where(MissingSkill.job_id == job.id)).all()

    assert score.recommendation_tier in {"Strong match", "Possible match"}
    assert len(missing) == 1
    assert missing[0].skill_name == "Databricks"
    assert missing[0].learning_priority == "high"


def test_excluded_technology_penalty_caps_score(db_session) -> None:
    user = seed_database(db_session)
    job = _create_scored_fixture_job(
        db_session,
        "Excluded Tech Role",
        "Data Engineer",
        ["Python", "SQL"],
        ["essential", "essential"],
        red_flags=["Power Platform: essential requirement (Requirements: must have Power Platform.)"],
    )

    score = score_job(db_session, job, user)

    assert score.recommendation_tier == "excluded"
    assert score.total_score <= Decimal("25")


def test_recommendation_tier_classification() -> None:
    assert recommendation_tier(90) == "Excellent match"
    assert recommendation_tier(75) == "Strong match"
    assert recommendation_tier(60) == "Possible match"
    assert recommendation_tier(45) == "Stretch role"
    assert recommendation_tier(20) == "Poor fit"


def test_explanation_generation(db_session) -> None:
    user = seed_database(db_session)
    job = _create_scored_fixture_job(
        db_session,
        "Explanation Role",
        "Data Engineer",
        ["Python", "SQL", "Airflow"],
        ["essential", "essential", "essential"],
    )

    score = score_job(db_session, job, user)

    assert "aligns with Data Engineer roles" in score.explanation
    assert "matches Python and SQL" in score.explanation
    assert "Airflow" in score.explanation


def test_fixture_jobs_score_expected_tiers(db_session) -> None:
    user = seed_database(db_session)
    excellent = _create_scored_fixture_job(
        db_session,
        "Excellent Match",
        "Data Engineer",
        ["Python", "SQL", "data modelling", "JSON"],
        ["essential", "essential", None, None],
    )
    strong = _create_scored_fixture_job(
        db_session,
        "Strong Match",
        "Analytics Engineer",
        ["Python", "SQL", "dbt", "Databricks"],
        ["essential", "essential", "nice_to_have", "essential"],
    )
    stretch = _create_scored_fixture_job(
        db_session,
        "Stretch Role",
        "AI Automation Engineer",
        ["RAG", "agents", "evaluation", "Python"],
        ["essential", "essential", "nice_to_have", None],
    )
    poor = _create_scored_fixture_job(
        db_session,
        "Poor Fit",
        "Marketing Manager",
        ["Databricks", "Terraform", "Azure Data Factory"],
        ["essential", "essential", "essential"],
        remote_type="onsite",
        location="Berlin",
    )
    excluded = _create_scored_fixture_job(
        db_session,
        "Excluded Role",
        "Data Engineer",
        ["Python", "SQL"],
        ["essential", "essential"],
        red_flags=["Power Automate: essential requirement (Requirements: must have Power Automate.)"],
    )

    scored = [score_job(db_session, job, user) for job in [excellent, strong, stretch, poor, excluded]]
    tiers = {score.job.title: score.recommendation_tier for score in scored}

    assert tiers["Excellent Match"] == "Excellent match"
    assert tiers["Strong Match"] in {"Strong match", "Possible match"}
    assert tiers["Stretch Role"] in {"Possible match", "Stretch role"}
    assert tiers["Poor Fit"] == "Poor fit"
    assert tiers["Excluded Role"] == "excluded"


def test_scoring_endpoints() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)
    with TestingSession() as db:
        seed_database(db)
        job = _create_scored_fixture_job(
            db,
            "API Score Role",
            "Internal Tools Engineer",
            ["Python", "SQL", "React", "FastAPI", "Databricks"],
            ["essential", "essential", None, None, "nice_to_have"],
        )
        job_id = job.id

    def override_get_db():
        with TestingSession() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        scored = client.post(f"/jobs/{job_id}/score")
        assert scored.status_code == 200
        assert scored.json()["job_id"] == job_id

        fetched = client.get(f"/jobs/{job_id}/score")
        assert fetched.status_code == 200

        all_response = client.post("/jobs/score-all")
        assert all_response.status_code == 200
        assert all_response.json()["jobs_scored"] == 1

        top = client.get("/scores/top")
        assert top.status_code == 200
        assert len(top.json()) == 1

        missing = client.get("/scores/missing-skills")
        assert missing.status_code == 200
        assert any(item["skill_name"] == "Databricks" for item in missing.json())

        recommendations = client.get("/scores/recommendations")
        assert recommendations.status_code == 200
        assert len(recommendations.json()) == 1
    finally:
        app.dependency_overrides.clear()


def test_score_all_skips_unanalysed_jobs(db_session) -> None:
    from app.services.scoring import score_all_jobs

    seed_database(db_session)
    source = JobSource(name="Unanalysed Source", base_url="https://example.invalid", source_type="fixture")
    db_session.add(source)
    db_session.flush()
    db_session.add(
        Job(
            source_id=source.id,
            source_job_id="unanalysed",
            canonical_url="https://example.invalid/jobs/unanalysed",
            title="Data Engineer",
        )
    )
    db_session.commit()

    result = score_all_jobs(db_session)

    assert result == {"jobs_scored": 0, "jobs_skipped": 1}


def _create_scored_fixture_job(
    db_session,
    title: str,
    role_family: str,
    skills: list[str],
    importances: list[str | None],
    *,
    red_flags: list[str] | None = None,
    remote_type: str = "remote",
    location: str = "Remote UK",
) -> Job:
    source = db_session.scalar(select(JobSource).where(JobSource.name == "Scoring Fixtures"))
    if source is None:
        source = JobSource(name="Scoring Fixtures", base_url="https://example.invalid", source_type="fixture")
        db_session.add(source)
        db_session.flush()
    slug = title.lower().replace(" ", "-")
    job = Job(
        source_id=source.id,
        source_job_id=slug,
        canonical_url=f"https://example.invalid/jobs/{slug}",
        title=title,
        company_name="Example Ltd",
        location=location,
        remote_type=remote_type,
        salary_min=Decimal("45000"),
        salary_max=Decimal("60000"),
        salary_min_raw=Decimal("45000"),
        salary_max_raw=Decimal("60000"),
        salary_period="year",
        normalized_annual_min=Decimal("45000"),
        normalized_annual_max=Decimal("60000"),
        description_text=(
            "Responsibilities include building useful systems for data and automation teams. "
            "Requirements include the listed skills and strong delivery habits."
        ),
        posted_at=datetime.now(tz=timezone.utc),
    )
    db_session.add(job)
    db_session.flush()
    db_session.add(
        JobAnalysis(
            job_id=job.id,
            role_family=role_family,
            seniority_level=None,
            role_focus=role_family.lower(),
            tools_detected=skills,
            responsibilities=["Responsibilities include building useful systems."],
            requirements=["Requirements include the listed skills."],
            nice_to_haves=[],
            red_flags=red_flags or [],
        )
    )
    for skill, importance in zip(skills, importances, strict=True):
        db_session.add(
            JobSkill(
                job_id=job.id,
                skill_name=skill,
                skill_category=None,
                importance=importance,
                evidence_text=f"Requirements include {skill}.",
            )
        )
    db_session.commit()
    db_session.refresh(job)
    return job
