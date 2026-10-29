from datetime import datetime, timedelta, timezone

from app.api import discovery as discovery_api
from app.db.models import Job, UserProfile
from app.db.session import get_db
from app.main import app
from tests.test_phase6_api import phase6_client


def _db():
    return next(app.dependency_overrides[get_db]())


def test_find_needs_a_cv_and_roles() -> None:
    with phase6_client() as (client, _):
        body = client.get("/find").json()

        assert body["has_cv"] is False
        assert body["jobs"] == []
        assert client.post("/discovery/run").status_code == 400


def test_find_lists_jobs_for_the_chosen_roles_and_tracks_what_was_done(monkeypatch) -> None:
    queued: list[tuple] = []
    monkeypatch.setattr(discovery_api, "enqueue_or_background", lambda _tasks, _func, *args, **_kw: queued.append(args))
    with phase6_client() as (client, ids):
        client.post("/profile/cv", json={"cv_text": "Warehouse supervisor. Skills: forklift, stock control"})
        saved = client.post("/profile/search", json={"roles": ["Data Engineer", " data engineer ", "AI Engineer"], "location": "Leeds", "remote_only": False})
        assert saved.json()["search_roles"] == ["Data Engineer", "AI Engineer"]
        db = _db()
        # A copy of the same role on another board is shown once.
        original = db.get(Job, ids["excellent"])
        db.add(Job(source_id=original.source_id, source_job_id="copy", canonical_url="https://example.invalid/copy", title=original.title, company_name=original.company_name, status="active", availability_status="active"))
        db.commit()
        db.close()

        found = client.get("/find").json()
        titles = [job["title"] for job in found["jobs"]]
        assert found["search"]["roles"] == ["Data Engineer", "AI Engineer"]
        assert found["search"]["location"] == "Leeds"
        # Jobs that don't fit the chosen roles are left out, even with a CV that doesn't mention them.
        assert sorted(titles) == ["Excellent Data Engineer", "Stretch AI Automation Engineer", "Strong Analytics Engineer"]
        assert all(job["status"] is None for job in found["jobs"])

        assert client.post("/find/apply", json={"job_ids": [ids["excellent"], ids["excellent"]]}).json() == {"changed": 1}
        assert client.post("/find/dismiss", json={"job_ids": [ids["stretch"]]}).json() == {"changed": 1}
        after = client.get("/find").json()["jobs"]
        # Jobs still to decide on come first, then the queued ones.
        assert [(job["title"], job["status"]) for job in after] == [("Strong Analytics Engineer", None), ("Excellent Data Engineer", "to_apply")]

        app_id = after[1]["application_id"]
        client.patch(f"/applications/{app_id}", json={"status": "applied"})
        applied = client.get("/find").json()["jobs"][1]
        assert applied["status"] == "applied" and applied["applied_at"] is not None
        # Applying again doesn't undo an application that's already been sent.
        assert client.post("/find/apply", json={"job_ids": [ids["excellent"]]}).json() == {"changed": 0}

        started = client.post("/discovery/run")
        assert started.status_code == 202 and queued
        assert client.get("/find").json()["search"]["searching"] is True


def test_old_listings_drop_off() -> None:
    with phase6_client() as (client, ids):
        client.post("/profile/cv", json={"cv_text": "Data engineer"})
        client.post("/profile/search", json={"roles": ["Data Engineer"]})
        db = _db()
        db.get(Job, ids["excellent"]).last_seen_at = datetime.now(tz=timezone.utc) - timedelta(days=30)
        db.commit()
        db.close()

        assert "Excellent Data Engineer" not in [job["title"] for job in client.get("/find").json()["jobs"]]


def test_search_preferences_fall_back_to_cv_roles() -> None:
    with phase6_client() as (client, _):
        client.post("/profile/cv", json={"cv_text": "TARGET ROLES\nData Engineer\n\nSKILLS\nPython, SQL"})
        db = _db()
        profile = db.query(UserProfile).one()
        assert profile.search_roles is None
        db.close()

        assert client.get("/find").json()["search"]["roles"] == ["Data Engineer"]
