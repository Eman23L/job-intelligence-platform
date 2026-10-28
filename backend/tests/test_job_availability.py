from decimal import Decimal

from sqlalchemy import select

from app.db.models import Application, Job, JobScore, JobSource, User
from app.services.applications import queue_top_matches
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


def test_unavailable_jobs_not_queued(db_session) -> None:
    job = _seed_scored_job(db_session, "Unavailable Data Engineer")
    job.availability_status = "unavailable"
    job.availability_reason = "HTTP 404"
    db_session.commit()

    job_ids = queue_top_matches(db_session, db_session.scalar(select(User)))

    assert job_ids == []
    assert db_session.scalar(select(Application).where(Application.job_id == job.id)) is None


def test_unknown_availability_jobs_can_be_queued(db_session) -> None:
    job = _seed_scored_job(db_session, "Unknown Availability Data Engineer")
    job.availability_status = "unknown"
    db_session.commit()

    job_ids = queue_top_matches(db_session, db_session.scalar(select(User)))

    assert job_ids == [job.id]
    application = db_session.scalar(select(Application).where(Application.job_id == job.id))
    assert application is not None
    assert application.status == "to_apply"


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
