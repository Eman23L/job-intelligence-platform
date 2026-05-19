from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from math import ceil

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Job, JobAnalysis, JobScore, JobSkill, MissingSkill, SavedJob, User
from app.schemas.database import JobDetail, JobListItem, PaginatedJobs


@dataclass(frozen=True)
class JobFilters:
    role_family: str | None = None
    recommendation_tier: str | None = None
    remote_type: str | None = None
    location: str | None = None
    company_name: str | None = None
    salary_min: Decimal | None = None
    salary_max: Decimal | None = None
    posted_after: datetime | None = None
    posted_before: datetime | None = None
    min_score: Decimal | None = None
    max_score: Decimal | None = None
    has_missing_skills: bool | None = None
    exclude_excluded: bool = False
    status: str | None = None


def list_jobs(
    db: Session,
    filters: JobFilters,
    *,
    sort: str = "total_score_desc",
    page: int = 1,
    page_size: int = 20,
    user: User | None = None,
) -> PaginatedJobs:
    page = max(page, 1)
    page_size = max(1, min(page_size, 100))
    rows = [_job_row(db, job, user) for job in db.scalars(select(Job)).all()]
    filtered = [row for row in rows if _matches_filters(row, filters)]
    filtered.sort(key=_sort_key(sort), reverse=_sort_reverse(sort))
    total_count = len(filtered)
    total_pages = ceil(total_count / page_size) if total_count else 0
    start = (page - 1) * page_size
    return PaginatedJobs(
        items=[row["item"] for row in filtered[start : start + page_size]],
        page=page,
        page_size=page_size,
        total_count=total_count,
        total_pages=total_pages,
    )


def job_detail(db: Session, job: Job, user: User | None = None) -> JobDetail:
    score = _score_for_job(db, job.id, user)
    analysis = db.scalar(select(JobAnalysis).where(JobAnalysis.job_id == job.id))
    missing = _missing_for_job(db, job.id, user)
    missing_names = {item.skill_name for item in missing}
    skills = db.scalars(select(JobSkill).where(JobSkill.job_id == job.id).order_by(JobSkill.skill_name)).all()
    matched = [skill for skill in skills if skill.skill_name not in missing_names]
    saved = _saved_for_job(db, job.id, user)
    return JobDetail(
        job=job,
        analysis=analysis,
        score=score,
        matched_skills=matched,
        missing_skills=missing,
        red_flags=(analysis.red_flags if analysis and analysis.red_flags else []),
        saved_status=saved.status if saved else None,
    )


def _job_row(db: Session, job: Job, user: User | None) -> dict:
    analysis = db.scalar(select(JobAnalysis).where(JobAnalysis.job_id == job.id))
    score = _score_for_job(db, job.id, user)
    missing_count = len(_missing_for_job(db, job.id, user))
    skills_count = len(db.scalars(select(JobSkill).where(JobSkill.job_id == job.id)).all())
    item = JobListItem(
        id=job.id,
        title=job.title,
        company_name=job.company_name,
        location=job.location,
        remote_type=job.remote_type,
        salary_min=job.salary_min,
        salary_max=job.salary_max,
        salary_currency=job.salary_currency,
        salary_min_raw=job.salary_min_raw,
        salary_max_raw=job.salary_max_raw,
        salary_period=job.salary_period,
        normalized_annual_min=job.normalized_annual_min,
        normalized_annual_max=job.normalized_annual_max,
        posted_at=job.posted_at,
        role_family=analysis.role_family if analysis else None,
        recommendation_tier=score.recommendation_tier if score else None,
        total_score=score.total_score if score else None,
        matched_skills_count=max(0, skills_count - missing_count),
        missing_skills_count=missing_count,
    )
    return {"job": job, "analysis": analysis, "score": score, "item": item}


def _matches_filters(row: dict, filters: JobFilters) -> bool:
    job: Job = row["job"]
    analysis: JobAnalysis | None = row["analysis"]
    score: JobScore | None = row["score"]
    item: JobListItem = row["item"]
    checks = [
        filters.role_family is None or (analysis and analysis.role_family == filters.role_family),
        filters.recommendation_tier is None or (score and score.recommendation_tier == filters.recommendation_tier),
        filters.remote_type is None or job.remote_type == filters.remote_type,
        filters.location is None or (job.location and filters.location.lower() in job.location.lower()),
        filters.company_name is None or (job.company_name and filters.company_name.lower() in job.company_name.lower()),
        filters.salary_min is None or (job.normalized_annual_max is not None and job.normalized_annual_max >= filters.salary_min),
        filters.salary_max is None or (job.normalized_annual_min is not None and job.normalized_annual_min <= filters.salary_max),
        filters.posted_after is None or (job.posted_at is not None and job.posted_at >= filters.posted_after),
        filters.posted_before is None or (job.posted_at is not None and job.posted_at <= filters.posted_before),
        filters.min_score is None or (score is not None and score.total_score >= filters.min_score),
        filters.max_score is None or (score is not None and score.total_score <= filters.max_score),
        filters.has_missing_skills is None or ((item.missing_skills_count > 0) == filters.has_missing_skills),
        not filters.exclude_excluded or not (score and score.recommendation_tier == "excluded"),
        filters.status is None or job.status == filters.status,
    ]
    return all(checks)


def _sort_key(sort: str):
    def key(row: dict):
        job: Job = row["job"]
        item: JobListItem = row["item"]
        if sort == "posted_at_desc":
            return job.posted_at or datetime.min
        if sort == "salary_max_desc":
            return job.normalized_annual_max or Decimal("0")
        if sort == "salary_min_desc":
            return job.normalized_annual_min or Decimal("0")
        if sort == "company_name_asc":
            return (job.company_name or "").lower()
        if sort == "title_asc":
            return job.title.lower()
        return item.total_score or Decimal("-1")

    return key


def _sort_reverse(sort: str) -> bool:
    return sort not in {"company_name_asc", "title_asc"}


def _score_for_job(db: Session, job_id: int, user: User | None) -> JobScore | None:
    query = select(JobScore).where(JobScore.job_id == job_id).order_by(JobScore.scored_at.desc())
    if user is not None:
        query = query.where(JobScore.user_id == user.id)
    return db.scalar(query)


def _missing_for_job(db: Session, job_id: int, user: User | None) -> list[MissingSkill]:
    query = select(MissingSkill).where(MissingSkill.job_id == job_id).order_by(MissingSkill.skill_name)
    if user is not None:
        query = query.where(MissingSkill.user_id == user.id)
    return list(db.scalars(query).all())


def _saved_for_job(db: Session, job_id: int, user: User | None) -> SavedJob | None:
    query = select(SavedJob).where(SavedJob.job_id == job_id)
    if user is not None:
        query = query.where(SavedJob.user_id == user.id)
    return db.scalar(query)
