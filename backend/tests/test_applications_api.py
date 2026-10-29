import pytest

from app.api import applications as applications_api
from app.db.models import Application, Job, JobSource, UserProfile
from app.db.session import get_db
from app.main import app
from tests.test_auth import _bearer, _invite_and_register, _setup_owner, auth_client
from tests.test_phase6_api import phase6_client


def test_log_external_application_and_update_status() -> None:
    with phase6_client() as (client, _):
        created = client.post(
            "/applications",
            json={"title": "Data Engineer", "company": "Acme", "url": "https://www.linkedin.com/jobs/view/1", "source": "linkedin", "status": "applied"},
        )
        app_id = created.json()["id"]
        interviewing = client.patch(f"/applications/{app_id}", json={"status": "interviewing", "notes": "Call on Friday"})
        bad = client.patch(f"/applications/{app_id}", json={"status": "teleported"})
        untitled = client.post("/applications", json={"title": "  ", "source": "indeed"})

        assert created.status_code == 201
        assert created.json()["job_id"] is None
        assert created.json()["source"] == "linkedin"
        assert created.json()["applied_at"] is not None
        assert interviewing.json()["status"] == "interviewing"
        assert interviewing.json()["notes"] == "Call on Friday"
        assert bad.status_code == 400
        assert untitled.status_code == 400
        assert client.delete(f"/applications/{app_id}").status_code == 204
        assert client.get("/applications").json()["items"] == []


def test_adding_the_same_job_twice_updates_one_row() -> None:
    with phase6_client() as (client, ids):
        first = client.post("/applications", json={"job_id": ids["strong"]})
        second = client.post("/applications", json={"job_id": ids["strong"], "status": "applied"})

        assert first.json()["id"] == second.json()["id"]
        assert second.json()["status"] == "applied"
        assert len(client.get("/applications").json()["items"]) == 1
        assert client.post("/applications", json={"job_id": 999_999}).status_code == 400


def test_apply_pack_has_job_and_candidate_details() -> None:
    with phase6_client() as (client, ids):
        assert client.get("/applications").json()["has_cv"] is False
        client.post("/profile/cv", json={"cv_text": "Python and SQL engineer"})
        assert client.get("/applications").json()["has_cv"] is True
        client.post(
            "/profile/application",
            json={"first_name": "Alex", "last_name": "Example", "phone": "07000 000000", "salary_expectation": "£50,000 – £75,000"},
        )
        app_id = client.post(f"/jobs/{ids['excellent']}/add-to-apply-list").json()["id"]

        pack = client.get(f"/applications/{app_id}/pack").json()

        assert pack["application"]["status"] == "to_apply"
        assert pack["job"]["title"] == "Excellent Data Engineer"
        assert pack["job"]["url"].startswith("https://example.invalid/jobs/")
        assert pack["job"]["salary"] == "£65,000 – £90,000 per year"
        assert pack["job"]["description"]
        assert pack["candidate"]["first_name"] == "Alex"
        assert pack["candidate"]["salary_expectation"] == "£50,000 – £75,000"
        assert pack["candidate"]["cv_text"] == "Python and SQL engineer"


@pytest.mark.real_auth
def test_applications_are_private_to_each_person(monkeypatch) -> None:
    with auth_client(monkeypatch) as (client, Session):
        owner = _setup_owner(client)
        friend = _invite_and_register(client, owner)
        with Session() as db:
            source = JobSource(name="Fixture", base_url="https://example.invalid", source_type="fixture")
            db.add(source)
            db.flush()
            job = Job(source_id=source.id, source_job_id="1", canonical_url="https://example.invalid/1", title="Data Engineer")
            db.add(job)
            db.add(UserProfile(user_id=1, cv_text="Owner CV"))
            db.commit()
            job_id = job.id

        owner_app = client.post(f"/jobs/{job_id}/mark-applied", headers=_bearer(owner)).json()

        assert client.get("/applications", headers=_bearer(friend)).json()["items"] == []
        assert client.get(f"/jobs/{job_id}", headers=_bearer(friend)).json()["application_status"] is None
        assert client.get(f"/applications/{owner_app['id']}/pack", headers=_bearer(friend)).status_code == 404
        assert client.patch(f"/applications/{owner_app['id']}", json={"status": "skipped"}, headers=_bearer(friend)).status_code == 404
        assert client.delete(f"/applications/{owner_app['id']}", headers=_bearer(friend)).status_code == 404
        assert client.get("/applications", headers=_bearer(owner)).json()["items"][0]["status"] == "applied"


def test_cv_file_download_returns_uploaded_file() -> None:
    with phase6_client() as (client, _):
        client.post("/profile/cv", json={"cv_text": "CV"})
        assert client.get("/profile/cv-file").status_code == 404
        upload = client.post("/profile/cv-file", files={"file": ("my cv.pdf", b"%PDF-1.4 fake", "application/pdf")})
        download = client.get("/profile/cv-file")

        assert upload.status_code == 200
        assert download.status_code == 200
        assert download.content == b"%PDF-1.4 fake"
        assert download.headers["content-type"] == "application/pdf"
        assert 'filename="my_cv.pdf"' in download.headers["content-disposition"]


def test_check_open_then_skip_closed_listings(monkeypatch) -> None:
    queued: list[tuple] = []
    monkeypatch.setattr(applications_api, "enqueue_or_background", lambda _tasks, _func, *args, **_kw: queued.append(args))
    with phase6_client() as (client, ids):
        assert client.post("/applications/check-open").status_code == 400
        open_id = client.post(f"/jobs/{ids['excellent']}/add-to-apply-list").json()["id"]
        closed_id = client.post(f"/jobs/{ids['strong']}/add-to-apply-list").json()["id"]

        started = client.post("/applications/check-open")

        assert started.status_code == 200
        assert sorted(queued[0][1]) == sorted([ids["excellent"], ids["strong"]])
        # The availability check finds one listing has gone.
        db = next(app.dependency_overrides[get_db]())
        db.get(Job, ids["strong"]).availability_status = "unavailable"
        db.commit()
        db.close()

        assert client.post("/applications/skip-closed").json() == {"skipped": 1}
        items = {item["id"]: item for item in client.get("/applications").json()["items"]}
        assert items[closed_id]["status"] == "skipped"
        assert "closed" in items[closed_id]["notes"]
        assert items[open_id]["status"] == "to_apply"
        assert client.post("/applications/skip-closed").json() == {"skipped": 0}


def test_approve_all_and_dismiss_new_matches() -> None:
    with phase6_client() as (client, ids):
        db = next(app.dependency_overrides[get_db]())
        for title in ("Data Engineer", "Platform Engineer", "Analytics Engineer"):
            db.add(Application(user_id=1, source="reed", title=title, company="Acme", status="suggested"))
        db.commit()
        suggested = {item.title: item.id for item in db.query(Application).filter_by(status="suggested")}
        db.close()

        assert client.get("/applications/counts").json() == {"suggested": 3}
        dismissed = client.post("/applications/decide", json={"approve": False, "ids": [suggested["Platform Engineer"]]})
        approved = client.post("/applications/decide", json={"approve": True})

        assert dismissed.json() == {"changed": 1}
        assert approved.json() == {"changed": 2}
        assert client.get("/applications/counts").json() == {"skipped": 1, "to_apply": 2}


@pytest.mark.real_auth
def test_import_key_adds_linkedin_and_indeed_jobs_to_new_matches(monkeypatch) -> None:
    with auth_client(monkeypatch) as (client, _):
        owner = _setup_owner(client)
        friend = _invite_and_register(client, owner)

        assert client.post("/suggestions/import", json={"jobs": []}).status_code == 401
        key = client.post("/suggestions/key", headers=_bearer(owner)).json()["key"]
        jobs = [
            {"source": "linkedin", "title": "Data Engineer", "company": "Acme", "url": "https://www.linkedin.com/jobs/view/123/?trk=alert", "reason": "Python and SQL match"},
            {"source": "linkedin", "title": "Data Engineer", "company": "Acme", "url": "https://www.linkedin.com/jobs/view/123/"},
            {"source": "indeed", "title": "AI Engineer", "company": "Beta", "url": "https://uk.indeed.com/viewjob?jk=abc"},
        ]
        imported = client.post("/suggestions/import", json={"jobs": jobs}, headers={"X-Import-Key": key})
        again = client.post("/suggestions/import", json={"jobs": jobs}, headers={"X-Import-Key": key})

        assert imported.json() == {"added": 2, "skipped": 1}
        assert again.json() == {"added": 0, "skipped": 3}
        owner_items = client.get("/applications", headers=_bearer(owner)).json()["items"]
        assert sorted((item["source"], item["status"]) for item in owner_items) == [("indeed", "suggested"), ("linkedin", "suggested")]
        assert next(item for item in owner_items if item["source"] == "linkedin")["notes"] == "Python and SQL match"
        assert client.get("/applications", headers=_bearer(friend)).json()["items"] == []
        assert client.get("/suggestions/key", headers=_bearer(owner)).json()["last_used_at"] is not None

        client.delete("/suggestions/key", headers=_bearer(owner))
        assert client.post("/suggestions/import", json={"jobs": jobs}, headers={"X-Import-Key": key}).status_code == 401
