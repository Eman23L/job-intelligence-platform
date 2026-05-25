from decimal import Decimal

from sqlalchemy import select

from app.db.models import Job, JobScore, JobSource, User
from app.services.applications import prepare_applications
from app.services.apply_strategy import calculate_apply_readiness, classify_apply_strategy, classify_job


def test_domain_classification_greenhouse(db_session) -> None:
    job = _job(db_session, "https://boards.greenhouse.io/acme/jobs/123")

    result = classify_job(job)

    assert result.strategy == "greenhouse"
    assert result.difficulty == "medium"


def test_missing_apply_url_blocked(db_session) -> None:
    job = _job(db_session, "")

    result = classify_apply_strategy(db_session, job)

    assert result.apply_strategy == "blocked"
    assert result.apply_difficulty == "blocked"


def test_workday_is_hard(db_session) -> None:
    job = _job(db_session, "https://acme.myworkdayjobs.com/en-US/careers/job/AI-Engineer")

    result = classify_job(job)

    assert result.strategy == "workday"
    assert result.difficulty == "hard"


def test_jobserve_modal_flow_is_easy(db_session) -> None:
    job = _job(
        db_session,
        "https://www.jobserve.com/gb/en/job/AI-Engineer",
        description_text="Job Application Apply now Email Address Upload CV Working status in UK",
    )

    result = classify_job(job)

    assert result.strategy == "jobserve_apply_easy"
    assert result.difficulty == "easy"
    assert result.reason == "JobServe hosted apply flow detected."


def test_jobserve_login_flow_is_medium(db_session) -> None:
    job = _job(
        db_session,
        "https://www.jobserve.com/gb/en/job/AI-Engineer",
        source_job_id="jobserve-login",
        description_text="Job Application Apply now Email Address Register or log in to your Job Seeker account",
    )

    result = classify_job(job)

    assert result.strategy == "jobserve_apply_medium"
    assert result.difficulty == "medium"
    assert result.reason == "JobServe login/register may be required."


def test_jobserve_external_workday_is_external_ats(db_session) -> None:
    job = _job(
        db_session,
        "https://www.jobserve.com/gb/en/job/AI-Engineer",
        source_job_id="jobserve-workday",
        description_text="Apply on company website via Workday myworkdayjobs",
    )

    result = classify_job(job)

    assert result.strategy == "external_ats"
    assert result.difficulty == "hard"
    assert result.reason == "External ATS detected: Workday."


def test_jobserve_no_apply_action_is_blocked(db_session) -> None:
    job = _job(
        db_session,
        "https://www.jobserve.com/gb/en/job/AI-Engineer",
        source_job_id="jobserve-blocked",
        description_text="This job expired. No apply button unavailable.",
    )

    result = classify_job(job)

    assert result.strategy == "blocked"
    assert result.difficulty == "blocked"
    assert result.reason == "No usable apply URL found."


def test_application_queue_excludes_blocked(db_session, monkeypatch) -> None:
    user = User(email="apply-strategy@example.invalid")
    source = JobSource(name="Apply Strategy", base_url="https://example.invalid", source_type="fixture")
    db_session.add_all([user, source])
    db_session.flush()
    blocked = _job(db_session, "", source=source, title="Blocked AI Engineer")
    easy = _job(db_session, "mailto:jobs@example.invalid", source=source, source_job_id="easy", title="Easy AI Engineer")
    blocked.availability_status = "active"
    easy.availability_status = "active"
    db_session.add_all(
        [
            JobScore(job_id=blocked.id, user_id=user.id, total_score=Decimal("90"), recommendation="apply", recommendation_tier="Strong match"),
            JobScore(job_id=easy.id, user_id=user.id, total_score=Decimal("80"), recommendation="apply", recommendation_tier="Strong match"),
        ]
    )
    db_session.commit()

    from app.services import applications

    monkeypatch.setattr(applications, "check_job_availability", lambda db, job: None)
    queued, job_ids = prepare_applications(db_session, user)

    assert queued == 1
    assert job_ids == [easy.id]
    assert db_session.get(Job, blocked.id).application_status == "not_started"


def test_application_queue_orders_easy_before_medium_and_hard(db_session, monkeypatch) -> None:
    user = User(email="apply-order@example.invalid")
    source = JobSource(name="Apply Order", base_url="https://example.invalid", source_type="fixture")
    db_session.add_all([user, source])
    db_session.flush()
    hard = _job(db_session, "https://acme.myworkdayjobs.com/jobs/1", source=source, source_job_id="hard", title="Hard AI Engineer")
    medium = _job(
        db_session,
        "https://www.jobserve.com/gb/en/job/login",
        source=source,
        source_job_id="medium",
        title="Medium AI Engineer",
        description_text="Job Application Apply now Register or log in to your Job Seeker account",
    )
    easy = _job(
        db_session,
        "https://www.jobserve.com/gb/en/job/easy",
        source=source,
        source_job_id="easy",
        title="Easy AI Engineer",
        description_text="Job Application Apply now Email Address Upload CV Working status in UK",
    )
    for job in [hard, medium, easy]:
        job.availability_status = "active"
        db_session.add(JobScore(job_id=job.id, user_id=user.id, total_score=Decimal("80"), recommendation="apply", recommendation_tier="Strong match"))
    db_session.commit()

    from app.services import applications

    monkeypatch.setattr(applications, "check_job_availability", lambda db, job: None)
    _, job_ids = prepare_applications(db_session, user)

    assert job_ids == [easy.id, medium.id, hard.id]


def test_readiness_score_prefers_easy_medium_active_jobs(db_session) -> None:
    hard = _job(db_session, "https://acme.myworkdayjobs.com/jobs/1")
    easy = _job(db_session, "mailto:jobs@example.invalid", source_job_id="easy")
    hard.availability_status = "active"
    easy.availability_status = "active"
    hard.apply_strategy = "workday"
    hard.apply_difficulty = "hard"
    easy.apply_strategy = "recruiter_email"
    easy.apply_difficulty = "easy"
    hard_score = JobScore(job_id=hard.id, user_id=1, total_score=Decimal("80"))
    easy_score = JobScore(job_id=easy.id, user_id=1, total_score=Decimal("80"))

    assert calculate_apply_readiness(easy, easy_score) > calculate_apply_readiness(hard, hard_score)


def _job(
    db_session,
    url: str,
    *,
    source=None,
    source_job_id: str = "job-1",
    title: str = "AI Engineer",
    description_text: str | None = None,
) -> Job:
    if source is None:
        source = db_session.scalar(select(JobSource).where(JobSource.name == "Fixture Source"))
        if source is None:
            source = JobSource(name="Fixture Source", base_url="https://example.invalid", source_type="fixture")
            db_session.add(source)
            db_session.flush()
    job = Job(
        source_id=source.id,
        source=source,
        source_job_id=source_job_id,
        canonical_url=url,
        title=title,
        company_name="Acme",
        description_text=description_text,
    )
    db_session.add(job)
    db_session.flush()
    return job
