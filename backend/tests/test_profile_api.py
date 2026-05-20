from collections.abc import Generator
from contextlib import contextmanager

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Base, User
from app.db.session import get_db
from app.main import app
from app.services.profile import extract_profile_fields


CV_TEXT = """
Skills:
Python, FastAPI, React, SQL, Docker

Experience:
- Senior Data Engineer at Example Ltd building APIs and pipelines.
- Analytics Engineer using dbt and PostgreSQL.

Projects:
- Job matching dashboard with Next.js and FastAPI.

Education:
- BSc Computer Science, Example University.

Preferred roles:
Data Engineer, Analytics Engineer

Preferences:
Location: London
Hybrid or remote roles
Salary: GBP 65000 - GBP 85000
"""

EMMANUEL_CV_SAMPLE = """
Emmanuel Example
LinkedIn: https://linkedin.com/in/emmanuel-example
LinkedIn: https://linkedin.com/in/emmanuel-example
Page 1 of 3

CAREER SUMMARY
Delivery focused Business Analyst and Service Designer with experience across public sector transformation.

SKILLS
Business Analysis, Service Design, Stakeholder Management, Agile, Project Management

EDUCATION
University of Roehampton
BA (Hons) Business Management

PROFESSIONAL EXPERIENCE
Senior Business Analyst - London Borough Council
- Led discovery workshops and mapped current-state services for a resident support programme.
- Delivered project work across case management, reporting and digital service redesign.

MANAGEMENT EXPERIENCE
Project Manager - Transformation Portfolio
- Coordinated multidisciplinary teams and managed delivery risks.

DESIGN EXPERIENCE
Service Designer - Customer Experience Programme
- Created journey maps, prototypes and service blueprints.

PROJECTS
Resident Portal Redesign - redesigned online application journeys and reduced avoidable contact.
Case Management Automation - delivered workflow automation and reporting dashboards.

CERTIFICATIONS
Agile Foundation

SECURITY CLEARANCE
BPSS cleared

Page 2 of 3
"""


@contextmanager
def profile_client() -> Generator[TestClient, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)
    with TestingSession() as db:
        db.add(User(email="profile@example.invalid"))
        db.commit()

    def override_get_db():
        with TestingSession() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_extract_profile_fields_from_cv_text() -> None:
    fields = extract_profile_fields(CV_TEXT)

    assert {"Python", "FastAPI", "React", "SQL", "Docker"}.issubset(set(fields["skills"]))
    assert fields["experience"]
    assert fields["projects"] == ["Job matching dashboard with Next.js and FastAPI"]
    assert fields["education"] == ["BSc Computer Science, Example University"]
    assert fields["preferred_roles"] == ["Data Engineer", "Analytics Engineer"]
    assert fields["summary"] == ""
    assert fields["preferences"] == {"remote": "hybrid", "location": "London", "salary": "65000-85000"}
    assert set(fields) == {"skills", "projects", "experience", "education", "preferred_roles", "summary", "preferences"}


def test_post_cv_stores_profile_and_get_returns_it() -> None:
    with profile_client() as client:
        created = client.post("/profile/cv", json={"cv_text": CV_TEXT})
        fetched = client.get("/profile")

        assert created.status_code == 200
        body = created.json()
        assert body["cv_text"].strip().startswith("Skills:")
        assert "Python" in body["skills"]
        assert body["preferred_roles"] == ["Data Engineer", "Analytics Engineer"]
        assert body["preferences"] == {"remote": "hybrid", "location": "London", "salary": "65000-85000"}
        assert body["location_preference"] == "London"
        assert body["remote_preference"] == "hybrid"
        assert body["salary_min_preference"] == "65000.00"
        assert body["salary_max_preference"] == "85000.00"
        assert fetched.status_code == 200
        assert fetched.json()["id"] == body["id"]


def test_post_cv_updates_existing_profile() -> None:
    with profile_client() as client:
        first = client.post("/profile/cv", json={"cv_text": CV_TEXT})
        second = client.post(
            "/profile/cv",
            json={
                "cv_text": "Skills:\nTypeScript, Next.js\nPreferred roles:\nFrontend Developer\nPreferences:\nRemote only"
            },
        )

        assert first.status_code == 200
        assert second.status_code == 200
        assert second.json()["id"] == first.json()["id"]
        assert second.json()["skills"] == ["TypeScript", "Next.js"]
        assert second.json()["preferred_roles"] == ["Frontend Developer"]
        assert second.json()["preferences"]["remote"] == "remote"
        assert second.json()["remote_preference"] == "remote"


def test_get_profile_returns_null_when_missing() -> None:
    with profile_client() as client:
        response = client.get("/profile")

        assert response.status_code == 200
        assert response.json() is None


def test_profile_endpoint_creates_default_user_when_missing() -> None:
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
        response = TestClient(app).post("/profile/cv", json={"cv_text": "Skills:\nPython"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["user_id"] == 1


def test_emmanuel_cv_section_parser_keeps_education_tight_and_extracts_projects() -> None:
    fields = extract_profile_fields(EMMANUEL_CV_SAMPLE)

    assert fields["projects"] == [
        "Resident Portal Redesign - redesigned online application journeys and reduced avoidable contact",
        "Case Management Automation - delivered workflow automation and reporting dashboards",
    ]
    assert fields["education"] == ["University of Roehampton", "BA (Hons) Business Management"]
    assert any("resident support programme" in item for item in fields["experience"])
    assert any("Case Management Automation" in item for item in fields["experience"])
    assert "Page 1 of 3" not in " ".join(fields["education"])
    assert "LinkedIn" not in fields["summary"]
    assert len(" ".join(fields["education"])) < 120
    assert {"Business Analyst", "Service Designer", "Project Manager"}.issubset(set(fields["preferred_roles"]))
