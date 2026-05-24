from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Job, JobScore, User
from app.schemas.database import ApplicationItem
from app.services.job_availability import QUEUEABLE_AVAILABILITY_STATUSES, check_job_availability

APPLICATION_STATUSES = {"not_started", "ready_to_apply", "opened", "applied", "skipped", "failed"}
QUEUEABLE_RECOMMENDATIONS = {"apply", "maybe"}
TERMINAL_APPLICATION_STATUSES = {"applied", "skipped"}


def set_application_status(db: Session, job: Job, status: str) -> Job:
    if status not in APPLICATION_STATUSES:
        raise ValueError("Invalid application status")
    if status in {"ready_to_apply", "opened", "applied"}:
        if _is_excluded(job, _score_for_job(db, job.id, None)):
            raise ValueError("Excluded jobs cannot be queued or applied to")
        if job.availability_status not in QUEUEABLE_AVAILABILITY_STATUSES:
            raise ValueError("Only jobs confirmed active can be queued or applied to")
    job.application_status = status
    db.commit()
    db.refresh(job)
    return job


def prepare_applications(db: Session, user: User | None = None) -> tuple[int, list[int]]:
    rows = db.execute(
        select(Job, JobScore)
        .join(JobScore, JobScore.job_id == Job.id)
        .where(
            Job.status != "excluded",
            Job.application_status.not_in(TERMINAL_APPLICATION_STATUSES),
            JobScore.total_score >= Decimal("70"),
            JobScore.recommendation_tier != "excluded",
        )
        .order_by(JobScore.total_score.desc(), Job.id)
    ).all()
    queued_ids: list[int] = []
    seen: set[int] = set()
    for job, score in rows:
        if user is not None and score.user_id != user.id:
            continue
        if job.id in seen:
            continue
        seen.add(job.id)
        if not _is_queueable(score):
            continue
        check_job_availability(db, job)
        if job.availability_status not in QUEUEABLE_AVAILABILITY_STATUSES:
            continue
        if job.application_status != "ready_to_apply":
            job.application_status = "ready_to_apply"
            queued_ids.append(job.id)
    db.commit()
    return len(queued_ids), queued_ids


def list_applications(db: Session, user: User | None = None) -> list[ApplicationItem]:
    score_query = select(
        JobScore.job_id.label("job_id"),
        JobScore.total_score.label("total_score"),
        JobScore.recommendation.label("recommendation"),
        JobScore.recommendation_tier.label("recommendation_tier"),
    )
    if user is not None:
        score_query = score_query.where(JobScore.user_id == user.id)
    score_subquery = score_query.subquery()

    rows = db.execute(
        select(
            Job.id,
            Job.title,
            Job.company_name,
            Job.location,
            Job.canonical_url,
            Job.application_status,
            Job.availability_status,
            Job.last_checked_at,
            Job.availability_reason,
            score_subquery.c.total_score,
            score_subquery.c.recommendation,
            score_subquery.c.recommendation_tier,
        )
        .outerjoin(score_subquery, score_subquery.c.job_id == Job.id)
        .where(Job.status != "excluded", Job.application_status.in_(("ready_to_apply", "opened", "failed")))
        .order_by(score_subquery.c.total_score.desc(), Job.id)
    ).all()
    return [
        ApplicationItem(
            job_id=row.id,
            title=row.title,
            company_name=row.company_name,
            location=row.location,
            apply_url=row.canonical_url,
            application_status=row.application_status,
            availability_status=row.availability_status,
            last_checked_at=row.last_checked_at,
            availability_reason=row.availability_reason,
            total_score=row.total_score,
            recommendation_tier=row.recommendation_tier,
            recommendation=row.recommendation or _recommendation_from_total(row.total_score),
        )
        for row in rows
    ]


def _is_queueable(score: JobScore) -> bool:
    recommendation = score.recommendation or _recommendation_from_total(score.total_score)
    return recommendation in QUEUEABLE_RECOMMENDATIONS


def _is_excluded(job: Job, score: JobScore | None) -> bool:
    return job.status == "excluded" or (score is not None and score.recommendation_tier == "excluded")


def _score_for_job(db: Session, job_id: int, user: User | None) -> JobScore | None:
    query = select(JobScore).where(JobScore.job_id == job_id).order_by(JobScore.scored_at.desc())
    if user is not None:
        query = query.where(JobScore.user_id == user.id)
    return db.scalar(query)


def _recommendation_from_total(total_score: Decimal | None) -> str | None:
    if total_score is None:
        return None
    if total_score >= Decimal("70"):
        return "apply"
    if total_score >= Decimal("50"):
        return "maybe"
    return "skip"
