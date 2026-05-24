from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import logging
from math import ceil
from time import perf_counter

from sqlalchemy import Select, asc, case, desc, func, nulls_last, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.models import Job, JobAnalysis, JobScore, JobSkill, MissingSkill, SavedJob, User
from app.schemas.database import JobDetail, JobListItem, PaginatedJobs
from app.services.job_scoring import recommendation_from_score

logger = logging.getLogger(__name__)
JOBS_STATEMENT_TIMEOUT_MS = 1800


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
    availability_status: str | None = None
    source_id: int | None = None


def list_jobs(
    db: Session,
    filters: JobFilters,
    *,
    sort: str = "total_score_desc",
    page: int = 1,
    page_size: int = 20,
    user: User | None = None,
) -> PaginatedJobs:
    started = perf_counter()
    timings: dict[str, int] = {}
    page = max(page, 1)
    page_size = max(1, min(page_size, 100))
    try:
        _set_statement_timeout(db)
        query_start = perf_counter()
        query = _job_list_query(filters, sort, user, timings=timings)
        timings["build_query_ms"] = int((perf_counter() - query_start) * 1000)
        count_query = select(func.count()).select_from(query.order_by(None).subquery())
        count_start = perf_counter()
        total_count = db.scalar(count_query) or 0
        timings["count_query_ms"] = int((perf_counter() - count_start) * 1000)
        total_pages = ceil(total_count / page_size) if total_count else 0
        offset = (page - 1) * page_size
        page_start = perf_counter()
        rows = db.execute(query.offset(offset).limit(page_size)).all()
        timings["pagination_query_ms"] = int((perf_counter() - page_start) * 1000)
        serialize_start = perf_counter()
        items = [_job_list_item(row) for row in rows]
        timings["serialization_ms"] = int((perf_counter() - serialize_start) * 1000)
    except SQLAlchemyError:
        logger.exception("jobs.list query failed timings=%s; returning empty fallback", timings)
        db.rollback()
        return PaginatedJobs(
            items=[],
            page=page,
            page_size=page_size,
            total_count=0,
            total_pages=0,
            warning="Jobs query timed out or failed. Try again shortly or narrow the filters.",
        )
    elapsed_ms = int((perf_counter() - started) * 1000)
    logger.info(
        "jobs.list completed total=%s returned=%s page=%s page_size=%s sort=%s elapsed_ms=%s timings=%s",
        total_count,
        len(items),
        page,
        page_size,
        sort,
        elapsed_ms,
        timings,
    )
    return PaginatedJobs(
        items=items,
        page=page,
        page_size=page_size,
        total_count=total_count,
        total_pages=total_pages,
    )


def _job_list_item(row) -> JobListItem:
    return JobListItem(
        id=row.id,
        title=row.title,
        company_name=row.company_name,
        location=row.location,
        remote_type=row.remote_type,
        salary_min=row.salary_min,
        salary_max=row.salary_max,
        salary_currency=row.salary_currency,
        salary_min_raw=row.salary_min_raw,
        salary_max_raw=row.salary_max_raw,
        salary_period=row.salary_period,
        normalized_annual_min=row.normalized_annual_min,
        normalized_annual_max=row.normalized_annual_max,
        posted_at=row.posted_at,
        role_family=row.role_family,
        recommendation_tier=row.recommendation_tier,
        total_score=row.total_score,
        recommendation=row.recommendation,
        matched_skills_count=max(0, int(row.skills_count or 0) - int(row.missing_skills_count or 0)),
        missing_skills_count=int(row.missing_skills_count or 0),
        status=row.status,
        application_status=row.application_status,
        availability_status=row.availability_status,
        last_checked_at=row.last_checked_at,
        availability_reason=row.availability_reason,
    )


def _job_list_query(filters: JobFilters, sort: str, user: User | None, timings: dict[str, int] | None = None) -> Select:
    base_started = perf_counter()
    skills_count = (
        select(JobSkill.job_id.label("job_id"), func.count(JobSkill.id).label("skills_count"))
        .group_by(JobSkill.job_id)
        .subquery()
    )
    missing_query = select(
        MissingSkill.job_id.label("job_id"),
        func.count(MissingSkill.id).label("missing_skills_count"),
    )
    if user is not None:
        missing_query = missing_query.where(MissingSkill.user_id == user.id)
    missing_count = missing_query.group_by(MissingSkill.job_id).subquery()

    score_query = select(
        JobScore.job_id.label("job_id"),
        JobScore.total_score.label("total_score"),
        func.coalesce(
            JobScore.recommendation,
            case(
                (JobScore.recommendation_tier == "excluded", "skip"),
                (JobScore.total_score >= 70, "apply"),
                (JobScore.total_score >= 50, "maybe"),
                else_="skip",
            ),
        ).label("recommendation"),
        JobScore.recommendation_tier.label("recommendation_tier"),
        JobScore.scored_at.label("scored_at"),
    )
    if user is not None:
        score_query = score_query.where(JobScore.user_id == user.id)
    score_subquery = score_query.subquery()
    if timings is not None:
        timings["score_application_data_query_build_ms"] = int((perf_counter() - base_started) * 1000)

    base_select_started = perf_counter()
    query = (
        select(
            Job.id,
            Job.title,
            Job.company_name,
            Job.location,
            Job.remote_type,
            Job.salary_min,
            Job.salary_max,
            Job.salary_currency,
            Job.salary_min_raw,
            Job.salary_max_raw,
            Job.salary_period,
            Job.normalized_annual_min,
            Job.normalized_annual_max,
            Job.posted_at,
            Job.status,
            Job.application_status,
            Job.availability_status,
            Job.last_checked_at,
            Job.availability_reason,
            JobAnalysis.role_family,
            score_subquery.c.recommendation_tier,
            score_subquery.c.total_score,
            score_subquery.c.recommendation,
            func.coalesce(skills_count.c.skills_count, 0).label("skills_count"),
            func.coalesce(missing_count.c.missing_skills_count, 0).label("missing_skills_count"),
        )
        .select_from(Job)
        .outerjoin(JobAnalysis, JobAnalysis.job_id == Job.id)
        .outerjoin(score_subquery, score_subquery.c.job_id == Job.id)
        .outerjoin(skills_count, skills_count.c.job_id == Job.id)
        .outerjoin(missing_count, missing_count.c.job_id == Job.id)
    )
    if timings is not None:
        timings["base_query_build_ms"] = int((perf_counter() - base_select_started) * 1000)

    filter_started = perf_counter()
    if filters.role_family is not None:
        query = query.where(JobAnalysis.role_family == filters.role_family)
    if filters.recommendation_tier is not None:
        query = query.where(score_subquery.c.recommendation_tier == filters.recommendation_tier)
    if filters.remote_type is not None:
        query = query.where(Job.remote_type == filters.remote_type)
    if filters.location is not None:
        query = query.where(Job.location.ilike(f"%{filters.location}%"))
    if filters.company_name is not None:
        query = query.where(Job.company_name.ilike(f"%{filters.company_name}%"))
    if filters.salary_min is not None:
        query = query.where(Job.normalized_annual_max.is_not(None), Job.normalized_annual_max >= filters.salary_min)
    if filters.salary_max is not None:
        query = query.where(Job.normalized_annual_min.is_not(None), Job.normalized_annual_min <= filters.salary_max)
    if filters.posted_after is not None:
        query = query.where(Job.posted_at.is_not(None), Job.posted_at >= filters.posted_after)
    if filters.posted_before is not None:
        query = query.where(Job.posted_at.is_not(None), Job.posted_at <= filters.posted_before)
    if filters.min_score is not None:
        query = query.where(score_subquery.c.total_score.is_not(None), score_subquery.c.total_score >= filters.min_score)
    if filters.max_score is not None:
        query = query.where(score_subquery.c.total_score.is_not(None), score_subquery.c.total_score <= filters.max_score)
    if filters.has_missing_skills is True:
        query = query.where(func.coalesce(missing_count.c.missing_skills_count, 0) > 0)
    elif filters.has_missing_skills is False:
        query = query.where(func.coalesce(missing_count.c.missing_skills_count, 0) == 0)
    if filters.exclude_excluded:
        query = query.where(
            Job.status != "excluded",
            (score_subquery.c.recommendation_tier.is_(None)) | (score_subquery.c.recommendation_tier != "excluded"),
        )
    if filters.status is not None:
        query = query.where(Job.status == filters.status)
    if filters.availability_status is not None:
        query = query.where(Job.availability_status == filters.availability_status)
    if filters.source_id is not None:
        query = query.where(Job.source_id == filters.source_id)
    if timings is not None:
        timings["filtering_ms"] = int((perf_counter() - filter_started) * 1000)

    sorting_started = perf_counter()
    query = query.order_by(*_sort_expressions(sort, score_subquery))
    if timings is not None:
        timings["sorting_ms"] = int((perf_counter() - sorting_started) * 1000)
    return query


def _sort_expressions(sort: str, score_subquery) -> tuple:
    if sort == "posted_at_desc":
        return (desc(Job.posted_at), desc(Job.id))
    if sort == "salary_max_desc":
        return (desc(Job.normalized_annual_max), desc(Job.id))
    if sort == "salary_min_desc":
        return (desc(Job.normalized_annual_min), desc(Job.id))
    if sort == "company_name_asc":
        return (asc(Job.company_name), asc(Job.id))
    if sort == "title_asc":
        return (asc(Job.title), asc(Job.id))
    return (nulls_last(desc(score_subquery.c.total_score)), desc(Job.id))


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
        recommendation=recommendation_from_score(score),
        matched_skills_count=max(0, skills_count - missing_count),
        missing_skills_count=missing_count,
        status=job.status,
        application_status=job.application_status,
        availability_status=job.availability_status,
        last_checked_at=job.last_checked_at,
        availability_reason=job.availability_reason,
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
        not filters.exclude_excluded or (job.status != "excluded" and not (score and score.recommendation_tier == "excluded")),
        filters.status is None or job.status == filters.status,
        filters.availability_status is None or job.availability_status == filters.availability_status,
        filters.source_id is None or job.source_id == filters.source_id,
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


def _set_statement_timeout(db: Session) -> None:
    if db.bind is None or db.bind.dialect.name != "postgresql":
        return
    db.execute(text(f"SET LOCAL statement_timeout = {JOBS_STATEMENT_TIMEOUT_MS}"))


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
