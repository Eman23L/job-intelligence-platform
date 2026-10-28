from collections.abc import Generator
from contextlib import contextmanager

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db.models import Base, Job, JobSource, SavedJob, User, UserProfile
from app.db.session import get_db
from app.main import app
from app.services.auth import hash_password, verify_password

pytestmark = pytest.mark.real_auth

SETUP_CODE = "test-setup-code"


@contextmanager
def auth_client(monkeypatch) -> Generator[tuple[TestClient, sessionmaker], None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)

    def override_get_db():
        with TestingSession() as db:
            yield db

    monkeypatch.setattr(settings, "owner_setup_code", SETUP_CODE)
    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app), TestingSession
    finally:
        app.dependency_overrides.pop(get_db, None)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _setup_owner(client: TestClient) -> str:
    response = client.post(
        "/auth/setup",
        json={"setup_code": SETUP_CODE, "email": "Owner@Example.com", "password": "correct horse", "name": "Owner"},
    )
    assert response.status_code == 200, response.text
    return response.json()["token"]


def _invite_and_register(client: TestClient, owner_token: str, email: str = "friend@example.com") -> str:
    code = client.post("/auth/invites", json={"note": "friend"}, headers=_bearer(owner_token)).json()["code"]
    response = client.post("/auth/register", json={"invite_code": code, "email": email, "password": "friend password"})
    assert response.status_code == 200, response.text
    return response.json()["token"]


def test_password_hash_round_trip() -> None:
    stored = hash_password("s3cret-pass")

    assert stored.startswith("scrypt$")
    assert verify_password("s3cret-pass", stored)
    assert not verify_password("wrong", stored)
    assert not verify_password("anything", None)
    assert not verify_password("anything", "not-a-hash")


def test_app_routes_require_sign_in(monkeypatch) -> None:
    with auth_client(monkeypatch) as (client, _):
        assert client.get("/jobs").status_code == 401
        assert client.get("/profile").status_code == 401
        assert client.get("/jobs", headers=_bearer("made-up-token")).status_code == 401
        assert client.get("/health").status_code == 200


def test_owner_setup_claims_existing_data_once(monkeypatch) -> None:
    with auth_client(monkeypatch) as (client, Session):
        with Session() as db:
            original = User(email="job-search-user@example.com")
            db.add(original)
            db.flush()
            db.add(UserProfile(user_id=original.id, cv_text="Existing CV"))
            db.commit()
            original_id = original.id

        assert client.get("/auth/status").json() == {"setup_required": True}
        wrong = client.post("/auth/setup", json={"setup_code": "nope", "email": "o@example.com", "password": "long enough"})
        assert wrong.status_code == 400

        token = _setup_owner(client)
        me = client.get("/auth/me", headers=_bearer(token)).json()

        assert me["id"] == original_id
        assert me["email"] == "owner@example.com"
        assert me["is_admin"] is True
        assert client.get("/profile", headers=_bearer(token)).json()["cv_text"] == "Existing CV"
        assert client.get("/auth/status").json() == {"setup_required": False}
        again = client.post("/auth/setup", json={"setup_code": SETUP_CODE, "email": "x@example.com", "password": "long enough"})
        assert again.status_code == 400


def test_owner_setup_disabled_without_setup_code(monkeypatch) -> None:
    with auth_client(monkeypatch) as (client, _):
        monkeypatch.setattr(settings, "owner_setup_code", "")
        response = client.post("/auth/setup", json={"setup_code": "", "email": "o@example.com", "password": "long enough"})

        assert response.status_code == 400


def test_login_logout(monkeypatch) -> None:
    with auth_client(monkeypatch) as (client, _):
        _setup_owner(client)

        bad = client.post("/auth/login", json={"email": "owner@example.com", "password": "wrong password"})
        good = client.post("/auth/login", json={"email": " OWNER@example.com ", "password": "correct horse"})
        token = good.json()["token"]

        assert bad.status_code == 401
        assert good.status_code == 200
        assert client.get("/auth/me", headers=_bearer(token)).status_code == 200
        assert client.post("/auth/logout", headers=_bearer(token)).status_code == 204
        assert client.get("/auth/me", headers=_bearer(token)).status_code == 401


def test_invites_are_single_use_and_owner_only(monkeypatch) -> None:
    with auth_client(monkeypatch) as (client, _):
        owner_token = _setup_owner(client)
        code = client.post("/auth/invites", json={}, headers=_bearer(owner_token)).json()["code"]

        first = client.post("/auth/register", json={"invite_code": code, "email": "friend@example.com", "password": "friend password"})
        reuse = client.post("/auth/register", json={"invite_code": code, "email": "other@example.com", "password": "other password"})
        friend_token = first.json()["token"]

        assert first.status_code == 200
        assert first.json()["user"]["is_admin"] is False
        assert reuse.status_code == 400
        assert client.post("/auth/invites", json={}, headers=_bearer(friend_token)).status_code == 403
        assert client.post("/auth/register", json={"invite_code": "bogus", "email": "z@example.com", "password": "long enough"}).status_code == 400


def test_each_account_sees_only_its_own_data(monkeypatch) -> None:
    with auth_client(monkeypatch) as (client, Session):
        owner_token = _setup_owner(client)
        friend_token = _invite_and_register(client, owner_token)
        client.post("/profile/cv", json={"cv_text": "Owner CV with Python and SQL"}, headers=_bearer(owner_token))
        with Session() as db:
            source = JobSource(name="Fixture", base_url="https://example.invalid", source_type="fixture")
            db.add(source)
            db.flush()
            job = Job(source_id=source.id, source_job_id="1", canonical_url="https://example.invalid/1", title="Data Engineer")
            db.add(job)
            db.commit()
            job_id = job.id

        client.post(f"/jobs/{job_id}/save", headers=_bearer(owner_token))

        assert client.get("/profile", headers=_bearer(friend_token)).json() is None
        assert client.get("/saved-jobs", headers=_bearer(friend_token)).json() == []
        owner_saved = client.get("/saved-jobs", headers=_bearer(owner_token)).json()
        assert len(owner_saved) == 1
        hijack = client.patch(f"/saved-jobs/{owner_saved[0]['id']}", json={"status": "rejected"}, headers=_bearer(friend_token))
        assert hijack.status_code == 404
        with Session() as db:
            assert db.scalar(select(SavedJob.status)) == "saved"


def test_members_cannot_delete_shared_jobs_or_sources(monkeypatch) -> None:
    with auth_client(monkeypatch) as (client, Session):
        owner_token = _setup_owner(client)
        friend_token = _invite_and_register(client, owner_token)
        with Session() as db:
            source = JobSource(name="Fixture", base_url="https://example.invalid", source_type="fixture")
            db.add(source)
            db.flush()
            job = Job(source_id=source.id, source_job_id="1", canonical_url="https://example.invalid/1", title="Data Engineer")
            db.add(job)
            db.commit()
            job_id, source_id = job.id, source.id

        assert client.delete(f"/jobs/{job_id}", headers=_bearer(friend_token)).status_code == 403
        assert client.post("/jobs/bulk-exclude", json={"job_ids": [job_id]}, headers=_bearer(friend_token)).status_code == 403
        assert client.delete(f"/sources/{source_id}", headers=_bearer(friend_token)).status_code == 403
        assert client.delete(f"/jobs/{job_id}", headers=_bearer(owner_token)).status_code == 200
