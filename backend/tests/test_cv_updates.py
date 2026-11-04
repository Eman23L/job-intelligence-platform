from datetime import timedelta

from app.api import profile as profile_api
from app.db.models import CvVersion, UserProfile
from app.db.session import get_db
from app.main import app
from app.services import cv_updates
from app.services.cv_pdf import parse_cv, render_cv_pdf
from app.services.run_tracking import utcnow
from tests.test_phase6_api import phase6_client

CV = """Alex Example
Data Engineer
Leeds, UK
07000 000000
alex@example.invalid
GitHub: https://github.com/example

CAREER SUMMARY
Data engineer building pipelines in Python and SQL on AWS for analytics teams across the business.

SKILLS
Python, SQL, AWS, Airflow

TARGET ROLES
Data Engineer, Analytics Engineer

PROFESSIONAL EXPERIENCE
Data Engineer, Acme, UK (2021 to present)
- Built Airflow pipelines that load sales data every hour.
- Cut report run time from 3 hours to 20 minutes.

SELECTED PROJECTS
Project: Weather Lake (Python, DuckDB)
Collects weather readings into a small data lake – with daily quality checks.

EDUCATION
BSc Computer Science, University of Leeds
"""

UPDATED = CV.replace("EDUCATION", "CERTIFICATIONS\nAWS Certified Cloud Practitioner (2026)\n\nEDUCATION")


def _db():
    return next(app.dependency_overrides[get_db]())


def test_cv_text_is_laid_out_as_a_pdf() -> None:
    cv = parse_cv(CV)

    assert cv.name == "Alex Example"
    assert cv.headline == "Data Engineer"
    assert cv.contact[0] == "Leeds, UK" and "GitHub: https://github.com/example" in cv.contact
    assert [section.heading for section in cv.sections] == [
        "CAREER SUMMARY",
        "SKILLS",
        "TARGET ROLES",
        "PROFESSIONAL EXPERIENCE",
        "SELECTED PROJECTS",
        "EDUCATION",
    ]
    # A line of capitalised skills isn't mistaken for a heading.
    assert parse_cv("Sam\n\nSKILLS\nAWS, GCP, SQL").sections[0].lines == ["AWS, GCP, SQL"]
    pdf = render_cv_pdf(CV + "\nEmoji 🚀 and curly ‘quotes’\n")
    assert pdf.startswith(b"%PDF") and len(pdf) > 1500


def test_claude_refreshes_the_cv_and_it_can_be_undone(monkeypatch) -> None:
    rescored: list[tuple] = []
    monkeypatch.setattr(profile_api, "enqueue_or_background", lambda _tasks, _func, *args, **_kw: rescored.append(args))
    with phase6_client() as (client, _):
        assert client.get("/profile/cv/updates").json()["due"] is False  # no CV yet
        client.post("/profile/cv", json={"cv_text": CV})
        status = client.get("/profile/cv/updates").json()
        assert status["due"] is True and status["versions"] == [] and status["refresh_every_days"] == 2

        assert client.post("/profile/cv/notes", json={"notes": "  Passed AWS Cloud Practitioner  "}).json()["notes"] == "Passed AWS Cloud Practitioner"

        # Nothing new: Claude only records that it checked, and the notes stay for next time.
        checked = client.post("/profile/cv/refresh", json={"cv_text": None, "changes": []}).json()
        assert checked["due"] is False and checked["checked_at"] and checked["notes"] == "Passed AWS Cloud Practitioner"
        assert rescored == []

        # Unsafe updates are refused.
        assert client.post("/profile/cv/refresh", json={"cv_text": UPDATED, "changes": []}).status_code == 400
        renamed = client.post("/profile/cv/refresh", json={"cv_text": UPDATED.replace("Alex Example", "Someone Else"), "changes": ["x"]})
        assert renamed.status_code == 400 and "Alex Example" in renamed.json()["detail"]
        assert client.post("/profile/cv/refresh", json={"cv_text": "Alex Example\nData Engineer", "changes": ["x"]}).status_code == 400

        refreshed = client.post("/profile/cv/refresh", json={"cv_text": UPDATED, "changes": ["Added AWS Cloud Practitioner (2026)."]}).json()
        assert refreshed["notes"] == ""
        assert [(v["changed_by"], v["change_note"]) for v in refreshed["versions"]] == [("claude", "Added AWS Cloud Practitioner (2026).")]
        assert len(rescored) == 1

        profile = client.get("/profile").json()
        assert "AWS Certified Cloud Practitioner" in profile["cv_text"]
        assert profile["cv_file_name"] == "Alex_Example_CV.pdf" and profile["cv_file_mime_type"] == "application/pdf"
        assert client.get("/profile/cv-file").content.startswith(b"%PDF")

        # Undo brings back the CV from before the update, and keeps the update in case it's wanted again.
        restored = client.post(f"/profile/cv/versions/{refreshed['versions'][0]['id']}/restore").json()
        assert "AWS Certified" not in restored["cv_text"]
        assert [v["changed_by"] for v in client.get("/profile/cv/updates").json()["versions"]] == ["undo", "claude"]
        assert client.post("/profile/cv/versions/9999/restore").status_code == 404

        # Editing by hand keeps the old text too; saving the same text again doesn't.
        client.post("/profile/cv", json={"cv_text": CV + "\nHobbies: chess"})
        client.post("/profile/cv", json={"cv_text": CV + "\nHobbies: chess"})
        assert [v["changed_by"] for v in client.get("/profile/cv/updates").json()["versions"]] == ["you", "undo", "claude"]

        made = client.post("/profile/cv-file/from-text").json()
        assert made["cv_file_name"] == "Alex_Example_CV.pdf"


def test_a_refresh_is_due_every_two_days() -> None:
    with phase6_client() as (client, _):
        client.post("/profile/cv", json={"cv_text": CV})
        db = _db()
        profile = db.query(UserProfile).one()
        profile.cv_checked_at = utcnow() - timedelta(hours=30)
        db.commit()
        assert client.get("/profile/cv/updates").json()["due"] is False

        # The next run, a little earlier in the day two days later, still counts.
        profile.cv_checked_at = utcnow() - timedelta(hours=44)
        db.commit()
        assert client.get("/profile/cv/updates").json()["due"] is True
        db.close()


def test_only_the_latest_versions_are_kept(monkeypatch) -> None:
    monkeypatch.setattr(cv_updates, "KEEP_VERSIONS", 3)
    with phase6_client() as (client, _):
        for number in range(6):
            client.post("/profile/cv", json={"cv_text": f"{CV}\nVersion {number}"})
        db = _db()
        assert db.query(CvVersion).count() == 3
        db.close()
