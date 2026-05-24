from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, Job, JobSource
from app.db.session import get_db
from app.main import app


def test_sources_endpoints() -> None:
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
    client = TestClient(app)
    try:
        payload = {
            "name": "API Source",
            "base_url": "https://example.invalid",
            "source_type": "board",
            "robots_url": "https://example.invalid/robots.txt",
            "scraping_allowed": True,
            "permission_notes": "Manual permission review complete.",
            "enabled": False,
            "last_reviewed_at": datetime.now(tz=timezone.utc).isoformat(),
        }

        created = client.post("/sources", json=payload)
        assert created.status_code == 201
        source_id = created.json()["id"]

        assert client.get("/sources").status_code == 200
        assert client.get(f"/sources/{source_id}").status_code == 200

        patched = client.patch(f"/sources/{source_id}", json={"rate_limit_per_minute": 20})
        assert patched.status_code == 200
        assert patched.json()["rate_limit_per_minute"] == 20

        enabled = client.post(f"/sources/{source_id}/enable")
        assert enabled.status_code == 200
        assert enabled.json()["enabled"] is True

        validation = client.post(f"/sources/{source_id}/validate-permission")
        assert validation.status_code == 200
        assert validation.json()["can_scrape"] is True

        disabled = client.post(f"/sources/{source_id}/disable")
        assert disabled.status_code == 200
        assert disabled.json()["enabled"] is False
    finally:
        app.dependency_overrides.clear()


def test_sources_list_empty_database() -> None:
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
        response = TestClient(app).get("/sources")

        assert response.status_code == 200
        assert response.json() == []
    finally:
        app.dependency_overrides.clear()


def test_sources_list_includes_url_patterns() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)
    with TestingSession() as db:
        db.add(
            JobSource(
                name="Pattern Source",
                base_url="https://example.invalid/careers",
                source_type="careers",
                robots_url="https://example.invalid/robots.txt",
                scraping_allowed=True,
                permission_notes="Reviewed manually.",
                rate_limit_per_minute=8,
                allowed_path_patterns=["/careers/", "/jobs/"],
                job_link_patterns=["/jobs/", "/vacancies/"],
                enabled=True,
                last_reviewed_at=datetime.now(tz=timezone.utc),
            )
        )
        db.commit()

    def override_get_db():
        with TestingSession() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = TestClient(app).get("/sources")

        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["allowed_path_patterns"] == ["/careers/", "/jobs/"]
        assert body[0]["job_link_patterns"] == ["/jobs/", "/vacancies/"]
    finally:
        app.dependency_overrides.clear()


def test_demo_scrape_endpoint_uses_fixture_only() -> None:
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
    client = TestClient(app)
    try:
        response = client.post("/sources/demo-scrape")

        assert response.status_code == 200
        assert response.json()["jobs_found"] == 2
        with TestingSession() as db:
            assert len(db.scalars(select(Job)).all()) == 2
    finally:
        app.dependency_overrides.clear()


def test_delete_source_defaults_to_disable_only() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)
    with TestingSession() as db:
        source = JobSource(name="Delete Me", base_url="https://example.invalid", source_type="fixture", enabled=True)
        db.add(source)
        db.commit()
        source_id = source.id

    def override_get_db():
        with TestingSession() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = TestClient(app).delete(f"/sources/{source_id}")
        assert response.status_code == 200
        assert response.json()["action"] == "disabled"
        with TestingSession() as db:
            source = db.get(JobSource, source_id)
            assert source is not None
            assert source.enabled is False
    finally:
        app.dependency_overrides.clear()


def test_delete_source_with_jobs_removes_source() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)
    with TestingSession() as db:
        source = JobSource(name="Delete Jobs", base_url="https://example.invalid", source_type="fixture", enabled=True)
        db.add(source)
        db.flush()
        db.add(
            Job(
                source_id=source.id,
                source_job_id="job-1",
                canonical_url="https://example.invalid/jobs/1",
                title="Fixture Job",
            )
        )
        db.commit()
        source_id = source.id

    def override_get_db():
        with TestingSession() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = TestClient(app).delete(f"/sources/{source_id}?delete_jobs=true")
        assert response.status_code == 200
        assert response.json()["action"] == "deleted"
        with TestingSession() as db:
            assert db.get(JobSource, source_id) is None
            assert db.scalars(select(Job)).all() == []
    finally:
        app.dependency_overrides.clear()
