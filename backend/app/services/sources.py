from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import JobSource
from app.schemas.database import JobSourceCreate, JobSourceUpdate, SourcePermissionValidation


def list_sources(db: Session) -> Sequence[JobSource]:
    return db.scalars(select(JobSource).order_by(JobSource.name)).all()


def get_source(db: Session, source_id: int) -> JobSource | None:
    return db.get(JobSource, source_id)


def create_source(db: Session, payload: JobSourceCreate) -> JobSource:
    source = JobSource(**payload.model_dump())
    db.add(source)
    db.commit()
    db.refresh(source)
    return source


def update_source(db: Session, source: JobSource, payload: JobSourceUpdate) -> JobSource:
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(source, field, value)
    db.commit()
    db.refresh(source)
    return source


def set_source_enabled(db: Session, source: JobSource, enabled: bool) -> JobSource:
    source.enabled = enabled
    db.commit()
    db.refresh(source)
    return source


def validate_source_permission(source: JobSource) -> SourcePermissionValidation:
    reasons: list[str] = []
    warnings: list[str] = []

    if not source.scraping_allowed:
        reasons.append("scraping_allowed is false")
    if not source.enabled:
        reasons.append("source is disabled")
    if not source.robots_url:
        reasons.append("robots_url is missing")
    if source.terms_url is not None and not source.terms_url.strip():
        reasons.append("terms_url is blank")
    if not source.permission_notes or not source.permission_notes.strip():
        reasons.append("permission_notes are missing")
    if source.last_reviewed_at is None:
        reasons.append("last_reviewed_at is missing")

    if source.rate_limit_per_minute <= 0:
        reasons.append("rate_limit_per_minute must be positive")
    elif source.rate_limit_per_minute > 60:
        warnings.append("rate_limit_per_minute is high for a polite scraper")

    return SourcePermissionValidation(
        source_id=source.id,
        can_scrape=not reasons,
        reasons=reasons,
        warnings=warnings,
    )
