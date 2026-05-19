from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import (
    Base,
    Job,
    JobAnalysis,
    JobScore,
    JobSkill,
    JobSource,
    MissingSkill,
    SavedJob,
    ScrapeRun,
)
from app.db.session import get_db
from app.main import app
from scripts.seed_data import seed_database


@contextmanager
def phase6_client() -> Generator[tuple[TestClient, dict[str, int]], None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)
    with TestingSession() as db:
        ids = _seed_phase6_data(db)

    def override_get_db():
        with TestingSession() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app), ids
    finally:
        app.dependency_overrides.clear()


def test_jobs_pagination() -> None:
    with phase6_client() as (client, _):
        response = client.get("/jobs?page=1&page_size=2")

        assert response.status_code == 200
        body = response.json()
        assert body["total_count"] == 4
        assert body["total_pages"] == 2
        assert len(body["items"]) == 2


def test_jobs_filters() -> None:
    with phase6_client() as (client, _):
        response = client.get("/jobs?role_family=Data Engineer&min_score=80&exclude_excluded=true")

        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) == 1
        assert items[0]["title"] == "Excellent Data Engineer"


def test_jobs_sorting() -> None:
    with phase6_client() as (client, _):
        response = client.get("/jobs?sort=salary_max_desc")

        assert response.status_code == 200
        assert response.json()["items"][0]["title"] == "Excellent Data Engineer"


def test_job_detail_response() -> None:
    with phase6_client() as (client, ids):
        response = client.get(f"/jobs/{ids['excellent']}")

        assert response.status_code == 200
        body = response.json()
        assert body["job"]["title"] == "Excellent Data Engineer"
        assert body["analysis"]["role_family"] == "Data Engineer"
        assert body["score"]["recommendation_tier"] == "Excellent match"
        assert len(body["matched_skills"]) >= 1
        assert body["saved_status"] == "saved"


def test_save_reject_and_mark_applied() -> None:
    with phase6_client() as (client, ids):
        save_response = client.post(f"/jobs/{ids['strong']}/save")
        reject_response = client.post(f"/jobs/{ids['strong']}/reject")
        applied_response = client.post(f"/jobs/{ids['strong']}/mark-applied")

        assert save_response.status_code == 200
        assert save_response.json()["status"] == "saved"
        assert reject_response.status_code == 200
        assert reject_response.json()["status"] == "rejected"
        assert applied_response.status_code == 200
        assert applied_response.json()["status"] == "applied"


def test_saved_jobs_list_and_patch() -> None:
    with phase6_client() as (client, _):
        listed = client.get("/saved-jobs")
        saved_id = listed.json()[0]["id"]
        patched = client.patch(f"/saved-jobs/{saved_id}", json={"status": "interviewing", "notes": "Phone screen"})

        assert listed.status_code == 200
        assert listed.json()[0]["job"] is not None
        assert patched.status_code == 200
        assert patched.json()["status"] == "interviewing"
        assert patched.json()["notes"] == "Phone screen"


def test_analytics_overview() -> None:
    with phase6_client() as (client, _):
        response = client.get("/analytics/overview")

        assert response.status_code == 200
        body = response.json()
        assert body["total_jobs"] == 4
        assert body["analysed_jobs"] == 4
        assert body["scored_jobs"] == 4
        assert body["excellent_matches"] == 1
        assert body["excluded_jobs"] == 1
        assert body["average_score"] == "60.25"
        assert body["newest_job_date"] is not None


def test_analytics_overview_empty_database() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)

    def override_get_db():
        with TestingSession() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = TestClient(app).get("/analytics/overview")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "total_jobs": 0,
        "analysed_jobs": 0,
        "scored_jobs": 0,
        "saved_jobs": 0,
        "applied_jobs": 0,
        "excellent_matches": 0,
        "strong_matches": 0,
        "stretch_roles": 0,
        "excluded_jobs": 0,
        "average_score": "0",
        "newest_job_date": None,
    }


def test_role_fit_analytics() -> None:
    with phase6_client() as (client, _):
        response = client.get("/analytics/role-fit")

        assert response.status_code == 200
        roles = {item["role_family"]: item for item in response.json()["items"]}
        assert roles["Data Engineer"]["count"] == 2
        assert "Excellent match" in roles["Data Engineer"]["recommendation_tiers"]


def test_skill_gaps_analytics() -> None:
    with phase6_client() as (client, _):
        response = client.get("/analytics/skill-gaps")

        assert response.status_code == 200
        body = response.json()
        assert any(item["skill_name"] == "dbt" for item in body["missing_skill_frequency"])
        assert any(item["skill_name"] == "RAG" for item in body["high_priority_missing_skills"])
        assert body["top_10_learning_priorities"]


def test_salary_analytics() -> None:
    with phase6_client() as (client, _):
        response = client.get("/analytics/salary")

        assert response.status_code == 200
        body = response.json()
        assert body["average_salary_min"] is not None
        assert body["missing_salary_count"] == 1
        assert any(item["group"] == "Data Engineer" for item in body["salary_by_role_family"])
        assert any(item["group"] == "remote" for item in body["salary_by_remote_type"])


def test_source_health_analytics() -> None:
    with phase6_client() as (client, _):
        response = client.get("/analytics/source-health")

        assert response.status_code == 200
        item = response.json()["items"][0]
        assert item["source_name"] == "Phase 6 Source"
        assert item["jobs_count"] == 4
        assert item["scrape_status"] == "success"
        assert item["jobs_found"] == 4


def _seed_phase6_data(db) -> dict[str, int]:
    user = seed_database(db)
    source = JobSource(name="Phase 6 Source", base_url="https://example.invalid", source_type="fixture")
    db.add(source)
    db.flush()
    db.add(
        ScrapeRun(
            source_id=source.id,
            status="success",
            finished_at=datetime.now(tz=timezone.utc),
            jobs_found=4,
            jobs_created=4,
            jobs_updated=0,
        )
    )
    now = datetime.now(tz=timezone.utc)
    jobs = {
        "excellent": _job(db, source.id, "Excellent Data Engineer", "remote", "Remote UK", 65000, 90000, now),
        "strong": _job(db, source.id, "Strong Analytics Engineer", "hybrid", "London", 50000, 70000, now - timedelta(days=2)),
        "stretch": _job(db, source.id, "Stretch AI Automation Engineer", "remote", "Remote UK", None, None, now - timedelta(days=10)),
        "excluded": _job(db, source.id, "Excluded Platform Role", "onsite", "Bristol", 45000, 55000, now - timedelta(days=20)),
    }
    _analysis_score(db, user.id, jobs["excellent"], "Data Engineer", "Excellent match", 92, ["Python", "SQL"], [])
    _analysis_score(
        db,
        user.id,
        jobs["strong"],
        "Analytics Engineer",
        "Strong match",
        76,
        ["Python", "SQL", "dbt"],
        [("dbt", "low")],
    )
    _analysis_score(
        db,
        user.id,
        jobs["stretch"],
        "AI Automation Engineer",
        "Stretch role",
        48,
        ["RAG", "agents"],
        [("RAG", "high"), ("agents", "high")],
    )
    _analysis_score(
        db,
        user.id,
        jobs["excluded"],
        "Data Engineer",
        "excluded",
        25,
        ["Python", "Power Automate"],
        [("Power Automate", "high")],
        red_flags=["Power Automate: essential requirement (must have Power Automate)"],
    )
    db.add(SavedJob(user_id=user.id, job_id=jobs["excellent"].id, status="saved", notes="Good fit"))
    db.add(SavedJob(user_id=user.id, job_id=jobs["strong"].id, status="applied"))
    db.commit()
    return {name: job.id for name, job in jobs.items()}


def _job(db, source_id, title, remote_type, location, salary_min, salary_max, posted_at):
    slug = title.lower().replace(" ", "-")
    job = Job(
        source_id=source_id,
        source_job_id=slug,
        canonical_url=f"https://example.invalid/jobs/{slug}",
        title=title,
        company_name="Example Ltd",
        location=location,
        remote_type=remote_type,
        salary_min=Decimal(str(salary_min)) if salary_min is not None else None,
        salary_max=Decimal(str(salary_max)) if salary_max is not None else None,
        salary_currency="GBP",
        salary_min_raw=Decimal(str(salary_min)) if salary_min is not None else None,
        salary_max_raw=Decimal(str(salary_max)) if salary_max is not None else None,
        salary_period="year" if salary_min is not None or salary_max is not None else None,
        normalized_annual_min=Decimal(str(salary_min)) if salary_min is not None else None,
        normalized_annual_max=Decimal(str(salary_max)) if salary_max is not None else None,
        posted_at=posted_at,
        status="active",
        description_text="A detailed fixture job description with requirements and responsibilities.",
    )
    db.add(job)
    db.flush()
    return job


def _analysis_score(db, user_id, job, role_family, tier, total_score, skills, missing, red_flags=None):
    db.add(
        JobAnalysis(
            job_id=job.id,
            role_family=role_family,
            seniority_level="mid",
            role_focus=role_family.lower(),
            tools_detected=skills,
            responsibilities=["Build useful systems."],
            requirements=["Use relevant skills."],
            nice_to_haves=[],
            red_flags=red_flags or [],
        )
    )
    for skill in skills:
        db.add(JobSkill(job_id=job.id, skill_name=skill, importance="essential", evidence_text=f"Use {skill}."))
    for skill, priority in missing:
        db.add(
            MissingSkill(
                job_id=job.id,
                user_id=user_id,
                skill_name=skill,
                importance="essential" if priority == "high" else "nice_to_have",
                learning_priority=priority,
                evidence_text=f"Use {skill}.",
            )
        )
    db.add(
        JobScore(
            job_id=job.id,
            user_id=user_id,
            total_score=Decimal(str(total_score)),
            role_match_score=Decimal("25"),
            skill_match_score=Decimal("20"),
            recommendation_tier=tier,
            explanation=f"{tier} fixture.",
        )
    )
