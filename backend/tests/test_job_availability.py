from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from app.db.models import Job, JobScore, JobSource, User
from app.services import applications
from app.services.applications import prepare_applications
from app.services.job_availability import FetchResult, check_job_availability


def test_availability_active_page(db_session) -> None:
    job = _seed_scored_job(db_session, "Senior Data Engineer")

    result = check_job_availability(
        db_session,
        job,
        fetcher=lambda _: FetchResult(
            final_url=job.canonical_url,
            status_code=200,
            text="""
            <html><body>
              <h1>Senior Data Engineer</h1>
              <p>Example Ltd</p>
              <a href="/apply">Apply now</a>
            </body></html>
            """,
            redirected=False,
        ),
    )

    assert result.availability_status == "active"
    assert result.last_checked_at is not None


def test_availability_expired_phrase(db_session) -> None:
    job = _seed_scored_job(db_session, "Senior Data Engineer")

    result = check_job_availability(
        db_session,
        job,
        fetcher=lambda _: FetchResult(
            final_url=job.canonical_url,
            status_code=200,
            text="<html><body>This job is no longer accepting applications.</body></html>",
            redirected=False,
        ),
    )

    assert result.availability_status == "expired"
    assert "no longer accepting" in (result.availability_reason or "")


def test_availability_404_page(db_session) -> None:
    job = _seed_scored_job(db_session, "Senior Data Engineer")

    result = check_job_availability(
        db_session,
        job,
        fetcher=lambda _: FetchResult(final_url=job.canonical_url, status_code=404, text="Not found", redirected=False),
    )

    assert result.availability_status == "unavailable"
    assert "404" in (result.availability_reason or "")


def test_availability_redirected_page(db_session) -> None:
    job = _seed_scored_job(db_session, "Senior Data Engineer")

    result = check_job_availability(
        db_session,
        job,
        fetcher=lambda _: FetchResult(
            final_url="https://example.com/jobs/archive",
            status_code=200,
            text="<html><body><a href='/apply'>Apply now</a></body></html>",
            redirected=True,
        ),
    )

    assert result.availability_status == "redirected"
    assert "redirected" in (result.availability_reason or "")


def test_unavailable_jobs_not_queued(db_session, monkeypatch) -> None:
    job = _seed_scored_job(db_session, "Unavailable Data Engineer")

    def mark_unavailable(db, candidate):
        candidate.availability_status = "unavailable"
        candidate.last_checked_at = datetime.now(tz=timezone.utc)
        candidate.availability_reason = "HTTP 404"
        db.commit()

    monkeypatch.setattr(applications, "check_job_availability", mark_unavailable)

    queued, job_ids = prepare_applications(db_session, db_session.scalar(select(User)))

    assert queued == 0
    assert job_ids == []
    db_session.refresh(job)
    assert job.application_status == "not_started"


def _seed_scored_job(db_session, title: str) -> Job:
    user = User(email=f"{title.lower().replace(' ', '-')}@example.com")
    source = JobSource(name=f"{title} Source", base_url="https://example.com", source_type="fixture")
    db_session.add_all([user, source])
    db_session.flush()
    job = Job(
        source_id=source.id,
        source_job_id=title.lower().replace(" ", "-"),
        canonical_url=f"https://example.com/jobs/{title.lower().replace(' ', '-')}",
        title=title,
        company_name="Example Ltd",
        status="active",
        application_status="not_started",
        description_text="A fixture job description.",
    )
    db_session.add(job)
    db_session.flush()
    db_session.add(
        JobScore(
            job_id=job.id,
            user_id=user.id,
            total_score=Decimal("85"),
            recommendation="apply",
            recommendation_tier="Strong match",
        )
    )
    db_session.commit()
    return job
