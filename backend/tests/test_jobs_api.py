from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, ExcludedTechnology, Job, JobSource
from app.db.session import get_db
from app.main import app


def test_analysis_endpoints() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)

    with TestingSession() as db:
        source = JobSource(name="API Jobs", base_url="https://example.invalid", source_type="fixture")
        db.add(source)
        db.flush()
        job = Job(
            source_id=source.id,
            source_job_id="api-001",
            canonical_url="https://example.invalid/jobs/api-001",
            title="Senior Internal Tools Engineer",
            company_name="Example Ltd",
            location="Hybrid London",
            description_text=(
                "Responsibilities include building internal tools. "
                "Required Python, FastAPI, React, SQL and PostgreSQL. "
                "Nice to have Docker."
            ),
        )
        db.add(job)
        db.add(ExcludedTechnology(name="Power Platform"))
        db.commit()
        job_id = job.id

    def override_get_db():
        with TestingSession() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        analysed = client.post(f"/jobs/{job_id}/analyse")
        assert analysed.status_code == 200
        assert analysed.json()["role_family"] == "Internal Tools Engineer"
        assert analysed.json()["seniority_level"] == "senior"

        analysis = client.get(f"/jobs/{job_id}/analysis")
        assert analysis.status_code == 200
        assert analysis.json()["role_focus"] == "internal tools"

        skills = client.get(f"/jobs/{job_id}/skills")
        assert skills.status_code == 200
        skill_names = {skill["skill_name"] for skill in skills.json()}
        assert {"Python", "FastAPI", "React", "SQL", "PostgreSQL"}.issubset(skill_names)

        all_response = client.post("/jobs/analyse-all")
        assert all_response.status_code == 200
        assert all_response.json()["jobs_analyzed"] == 1
    finally:
        app.dependency_overrides.clear()
