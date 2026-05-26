from collections.abc import Generator
from contextlib import contextmanager
from io import BytesIO

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
Emmanuel Bamgbala
Milton Keynes, Broughton, United Kingdom
Email: emmanuel@example.com
Phone: 07123 456789
Portfolio: https://example.com
LinkedIn: https://linkedin.com/in/emmanuel-bamgbala
AtkinsRealis - Baseline / Reference
Page 1 of 5

CAREER SUMMARY
Technologist building automation, reporting and data products. Does not require sponsorship.

SKILLS
Python, JavaScript, TypeScript, SQL, React, Next.js, Tailwind CSS
Power BI, Power Apps, Power Automate, Power Query, Excel, SharePoint
BeautifulSoup, Requests, JSON, Cloudflare, Supabase, Linux, WSL, Bash, YAML, Git
Data Pipelines, Web Scraping, Workflow Automation, Dashboard Reporting, Systems Integration, Role-Based Access Control, Data Modelling, Automation, AI

EDUCATION
Emmanuel Bamgbala
Digital and Technological Solutions
University of Roehampton
2:1
United Kingdom
LinkedIn: https://linkedin.com/in/emmanuel-bamgbala
AtkinsRealis - Baseline / Reference

PROFESSIONAL EXPERIENCE
Software Engineer - Portfolio Projects
- Project: GetFlow - Contributions Management Platform - Built role-based contribution workflows with Supabase and Cloudflare.
- Project: UK Homelessness Support Data Pipeline - Built Python data pipelines using BeautifulSoup, Requests and JSON.
- Project: Self-Hosted Remote Development Environment - Built Linux, WSL, Bash and YAML automation.

PROJECTS
Project: Opportunity DecisionAI - AI-assisted opportunity qualification and dashboard reporting.
Project: Power Platform & Reporting Solutions - Power Apps and Power Automate workflow automation with SharePoint integration.
Project: Power BI Timesheet Dashboard - Power Query data modelling and reporting.

SECURITY CLEARANCE
SC Cleared
BPSS Cleared

REFERENCES
Available on request
Page 2 of 5
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
    assert fields["preferences"] == {
        "remote": "hybrid",
        "location": "",
        "salary": "65000-85000",
        "work_authorization": "",
        "target_seniority": "mid_senior",
    }
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
        assert body["preferences"] == {
            "remote": "hybrid",
            "location": "",
            "salary": "65000-85000",
            "work_authorization": "",
            "target_seniority": "mid_senior",
        }
        assert body["location_preference"] is None
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


def test_application_profile_saves_jobserve_required_fields() -> None:
    with profile_client() as client:
        response = client.post(
            "/profile/application",
            json={
                "email": "candidate@example.invalid",
                "availability_notice": "Immediate",
                "salary_expectation_gbp": 65000,
                "travel_distance_miles": 25,
                "minimum_apply_score": 80,
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["email"] == "candidate@example.invalid"
        assert body["availability_notice"] == "Immediate"
        assert body["salary_expectation_gbp"] == 65000
        assert body["travel_distance_miles"] == 25


def test_cv_file_upload_stores_worker_accessible_blob() -> None:
    with profile_client() as client:
        response = client.post(
            "/profile/cv-file",
            files={"file": ("cv.pdf", BytesIO(b"%PDF-1.4 fixture"), "application/pdf")},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["cv_file_name"] == "cv.pdf"
        assert body["cv_file_mime_type"] == "application/pdf"
        assert body["cv_file_size"] == len(b"%PDF-1.4 fixture")


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

    project_text = " ".join(fields["projects"])
    for project_name in [
        "GetFlow - Contributions Management Platform",
        "UK Homelessness Support Data Pipeline",
        "Self-Hosted Remote Development Environment",
        "Opportunity DecisionAI",
        "Power Platform & Reporting Solutions",
        "Power BI Timesheet Dashboard",
    ]:
        assert project_name in project_text
    assert fields["education"] == ["Digital and Technological Solutions", "University of Roehampton", "2:1"]
    assert any("UK Homelessness Support Data Pipeline" in item for item in fields["experience"])
    for skill in ["Power BI", "Power Apps", "Power Automate", "Cloudflare", "Supabase", "BeautifulSoup", "Requests", "WSL", "Bash", "YAML"]:
        assert skill in fields["skills"]
    assert fields["preferences"]["location"] == "Milton Keynes / Broughton / United Kingdom"
    assert fields["preferences"]["work_authorization"] == "does not require sponsorship; SC Cleared; BPSS Cleared"
    assert "Page 1 of 5" not in " ".join(fields["education"])
    assert "LinkedIn" not in fields["summary"]
    assert "Emmanuel Bamgbala" not in " ".join(fields["education"])
    assert "AtkinsRealis" not in " ".join(fields["education"])
    assert len(" ".join(fields["education"])) < 120
