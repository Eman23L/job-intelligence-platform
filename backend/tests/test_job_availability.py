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


def test_availability_replaced_when_title_company_change(db_session) -> None:
    job = _seed_scored_job(db_session, "AI Delivery Manager", company="Original Co")

    result = check_job_availability(
        db_session,
        job,
        fetcher=lambda _: FetchResult(
            final_url=job.canonical_url,
            status_code=200,
            text="""
            <html><body>
              <h1>Audit Policy Leader</h1>
              <p class="company">Different Co</p>
              <a href="/apply">Apply now</a>
            </body></html>
            """,
            redirected=False,
        ),
    )

    assert result.availability_status == "replaced"
    assert "title changed from AI Delivery Manager to Audit Policy Leader" in (result.availability_reason or "")


def test_jobserve_search_page_with_different_selected_job_is_replaced(db_session) -> None:
    job = _seed_scored_job(
        db_session,
        "AI Delivery Manager",
        company="Original Co",
        canonical_url="https://www.jobserve.com/gb/en/JobSearch.aspx?shid=abc",
    )

    result = check_job_availability(
        db_session,
        job,
        fetcher=lambda _: FetchResult(
            final_url=job.canonical_url,
            status_code=200,
            text="""
            <html><body>
              <input type="hidden" id="jobIDs" value="A123#B456" />
              <section class="selected">
                <h1 class="job-title">Audit Policy Leader</h1>
                <span class="company">Different Co</span>
                <a href="/apply/B456">Apply now</a>
              </section>
            </body></html>
            """,
            redirected=False,
        ),
    )

    assert result.availability_status == "replaced"
    assert "JobServe page loaded but selected job title changed" in (result.availability_reason or "")


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


def _seed_scored_job(db_session, title: str, *, company: str = "Example Ltd", canonical_url: str | None = None) -> Job:
    user = User(email=f"{title.lower().replace(' ', '-')}@example.com")
    source = JobSource(name=f"{title} Source", base_url="https://example.com", source_type="fixture")
    db_session.add_all([user, source])
    db_session.flush()
    job = Job(
        source_id=source.id,
        source_job_id=title.lower().replace(" ", "-"),
        canonical_url=canonical_url or f"https://example.com/jobs/{title.lower().replace(' ', '-')}",
        original_title=title,
        original_company=company,
        original_location=None,
        original_salary=None,
        original_external_id=title.lower().replace(" ", "-"),
        title=title,
        company_name=company,
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
