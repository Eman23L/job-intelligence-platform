from datetime import datetime, timezone

from app.db.models import JobSource
from app.schemas.database import JobSourceCreate, JobSourceUpdate
from app.services.sources import create_source, set_source_enabled, update_source, validate_source_permission


def test_source_permission_validation_requires_metadata(db_session) -> None:
    source = JobSource(
        name="Unreviewed",
        base_url="https://example.invalid",
        source_type="board",
        scraping_allowed=False,
        enabled=False,
    )
    db_session.add(source)
    db_session.commit()

    result = validate_source_permission(source)

    assert result.can_scrape is False
    assert "scraping_allowed is false" in result.reasons
    assert "source is disabled" in result.reasons
    assert "robots_url is missing" in result.reasons
    assert "permission_notes are missing" in result.reasons
    assert "last_reviewed_at is missing" in result.reasons


def test_source_permission_validation_allows_reviewed_source(db_session) -> None:
    source = JobSource(
        name="Reviewed",
        base_url="https://example.invalid",
        source_type="board",
        robots_url="https://example.invalid/robots.txt",
        scraping_allowed=True,
        permission_notes="Allowed by published policy.",
        enabled=True,
        last_reviewed_at=datetime.now(tz=timezone.utc),
    )
    db_session.add(source)
    db_session.commit()

    result = validate_source_permission(source)

    assert result.can_scrape is True
    assert result.reasons == []


def test_source_service_crud(db_session) -> None:
    source = create_source(
        db_session,
        JobSourceCreate(
            name="Example",
            base_url="https://example.invalid",
            source_type="board",
        ),
    )

    updated = update_source(db_session, source, JobSourceUpdate(permission_notes="Reviewed manually"))
    enabled = set_source_enabled(db_session, updated, True)

    assert enabled.id == source.id
    assert enabled.enabled is True
    assert enabled.permission_notes == "Reviewed manually"
