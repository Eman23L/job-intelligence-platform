from datetime import datetime
from decimal import Decimal
import logging
from time import perf_counter

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import event, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.db.models import Job, JobAnalysis, JobScore, JobSkill, User
from app.db.session import get_db
from app.schemas.database import (
    BulkJobActionResult,
    JobAvailabilityCheckRequest,
    JobAvailabilityResult,
    JobAvailabilityRunStart,
    JobAvailabilityRunStatus,
    JobAnalysisRead,
    JobDetail,
    JobIdsRequest,
    JobRescoreRunStart,
    JobRescoreRunStatus,
    JobScoreRead,
    JobScorecard,
    JobSkillRead,
    PaginatedJobs,
    SavedJobRead,
)
from app.services.analysis import analyse_all_jobs, analyse_job
from app.services.applications import set_application_status
from app.services.job_cleanup import delete_job, delete_jobs, exclude_jobs
from app.services.job_discovery import JobFilters, job_detail, list_jobs
from app.services.job_availability import (
    check_job_availability,
    get_availability_run_status,
    run_availability_background,
    start_availability_run,
)
from app.services.job_scoring import scorecard_for_job
from app.services.rescore_runs import cancel_rescore_run, get_rescore_run_status, retry_rescore_run, run_rescore_background, start_rescore_run
from app.services.saved_jobs import set_saved_job_status
from app.services.scoring import score_all_jobs, score_job

router = APIRouter(prefix="/jobs", tags=["jobs"])
logger = logging.getLogger(__name__)


def _get_job_or_404(db: Session, job_id: int) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job


def _default_user(db: Session) -> User:
    user = db.scalar(select(User).order_by(User.id))
    if user is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No seeded user found")
    return user


@router.get("", response_model=PaginatedJobs)
def get_jobs(
    role_family: str | None = None,
    recommendation_tier: str | None = None,
    remote_type: str | None = None,
    location: str | None = None,
    company_name: str | None = None,
    salary_min: Decimal | None = None,
    salary_max: Decimal | None = None,
    posted_after: datetime | None = None,
    posted_before: datetime | None = None,
    min_score: Decimal | None = None,
    max_score: Decimal | None = None,
    has_missing_skills: bool | None = None,
    exclude_excluded: bool = False,
    status: str | None = None,
    availability_status: str | None = None,
    source_id: int | None = None,
    sort: str = Query(default="total_score_desc"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    filters = JobFilters(
        role_family=role_family,
        recommendation_tier=recommendation_tier,
        remote_type=remote_type,
        location=location,
        company_name=company_name,
        salary_min=salary_min,
        salary_max=salary_max,
        posted_after=posted_after,
        posted_before=posted_before,
        min_score=min_score,
        max_score=max_score,
        has_missing_skills=has_missing_skills,
        exclude_excluded=exclude_excluded,
        status=status,
        availability_status=availability_status,
        source_id=source_id,
    )
    started = perf_counter()
    query_count = 0

    def count_query(*args):
        nonlocal query_count
        query_count += 1

    bind = db.get_bind()
    if isinstance(bind, Engine):
        event.listen(bind, "before_cursor_execute", count_query)
    try:
        result = list_jobs(db, filters, sort=sort, page=page, page_size=page_size)
        return result
    except Exception:
        logger.exception("jobs.list failed page=%s page_size=%s sort=%s", page, page_size, sort)
        raise
    finally:
        if isinstance(bind, Engine):
            event.remove(bind, "before_cursor_execute", count_query)
        elapsed_ms = int((perf_counter() - started) * 1000)
        rows_returned = len(result.items) if "result" in locals() else 0
        logger.info(
            "jobs.list request finished page=%s page_size=%s sort=%s elapsed_ms=%s rows_returned=%s query_count=%s",
            page,
            page_size,
            sort,
            elapsed_ms,
            rows_returned,
            query_count,
        )


@router.post("/bulk-delete", response_model=BulkJobActionResult)
def bulk_delete_jobs(payload: JobIdsRequest, db: Session = Depends(get_db)):
    deleted_ids = delete_jobs(db, payload.job_ids)
    return BulkJobActionResult(affected=len(deleted_ids), job_ids=deleted_ids)


@router.post("/bulk-exclude", response_model=BulkJobActionResult)
def bulk_exclude_jobs(payload: JobIdsRequest, db: Session = Depends(get_db)):
    excluded_ids = exclude_jobs(db, payload.job_ids)
    return BulkJobActionResult(affected=len(excluded_ids), job_ids=excluded_ids)


@router.post("/check-availability", response_model=JobAvailabilityRunStart)
def bulk_check_availability(background_tasks: BackgroundTasks, payload: JobAvailabilityCheckRequest | None = None, db: Session = Depends(get_db)):
    job_ids = payload.job_ids if payload else None
    started, created = start_availability_run(db, job_ids)
    if created:
        background_tasks.add_task(run_availability_background, started.run_id, job_ids)
    return started


@router.get("/availability-runs/{run_id}", response_model=JobAvailabilityRunStatus)
def get_availability_run(run_id: int, db: Session = Depends(get_db)):
    result = get_availability_run_status(db, run_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Availability run not found")
    return result


@router.post("/rescore", response_model=JobRescoreRunStart)
def rescore_all_jobs(background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    try:
        user = _default_user(db)
        started, created = start_rescore_run(db, user)
        if created:
            background_tasks.add_task(run_rescore_background, started.run_id, user.id)
        return started
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/rescore-runs/{run_id}", response_model=JobRescoreRunStatus)
def get_rescore_run(run_id: int, db: Session = Depends(get_db)):
    result = get_rescore_run_status(db, run_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rescore run not found")
    return result


@router.post("/rescore-runs/{run_id}/retry", response_model=JobRescoreRunStart)
def retry_rescore(run_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    user = _default_user(db)
    started, created = retry_rescore_run(db, run_id, user)
    if started is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rescore run not found")
    if created:
        background_tasks.add_task(run_rescore_background, started.run_id, user.id)
    return started


@router.post("/rescore-runs/{run_id}/cancel", response_model=JobRescoreRunStart)
def cancel_rescore(run_id: int, db: Session = Depends(get_db)):
    result = cancel_rescore_run(db, run_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rescore run not found")
    return result


@router.get("/{job_id}", response_model=JobDetail)
def get_job_detail(job_id: int, db: Session = Depends(get_db)):
    return job_detail(db, _get_job_or_404(db, job_id))


@router.post("/{job_id}/check-availability", response_model=JobAvailabilityResult)
def check_single_availability(job_id: int, db: Session = Depends(get_db)):
    return check_job_availability(db, _get_job_or_404(db, job_id))


@router.delete("/{job_id}", response_model=BulkJobActionResult)
def delete_single_job(job_id: int, db: Session = Depends(get_db)):
    job = _get_job_or_404(db, job_id)
    delete_job(db, job)
    return BulkJobActionResult(affected=1, job_ids=[job_id])


@router.post("/{job_id}/analyse", response_model=JobAnalysisRead)
def analyse_single_job(job_id: int, db: Session = Depends(get_db)):
    job = _get_job_or_404(db, job_id)
    return analyse_job(db, job)


@router.post("/analyse-all")
def analyse_all(db: Session = Depends(get_db)):
    return analyse_all_jobs(db)


@router.get("/{job_id}/analysis", response_model=JobAnalysisRead)
def get_job_analysis(job_id: int, db: Session = Depends(get_db)):
    _get_job_or_404(db, job_id)
    analysis = db.scalar(select(JobAnalysis).where(JobAnalysis.job_id == job_id))
    if analysis is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job analysis not found")
    return analysis


@router.get("/{job_id}/skills", response_model=list[JobSkillRead])
def get_job_skills(job_id: int, db: Session = Depends(get_db)):
    _get_job_or_404(db, job_id)
    return db.scalars(select(JobSkill).where(JobSkill.job_id == job_id).order_by(JobSkill.skill_name)).all()


@router.post("/{job_id}/score", response_model=JobScoreRead)
def score_single_job(job_id: int, db: Session = Depends(get_db)):
    job = _get_job_or_404(db, job_id)
    try:
        return score_job(db, job)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/score-all")
def score_all(db: Session = Depends(get_db)):
    try:
        return score_all_jobs(db)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/{job_id}/score", response_model=JobScoreRead)
def get_job_score(job_id: int, db: Session = Depends(get_db)):
    _get_job_or_404(db, job_id)
    score = db.scalar(select(JobScore).where(JobScore.job_id == job_id).order_by(JobScore.scored_at.desc()))
    if score is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job score not found")
    return score


@router.get("/{job_id}/scorecard", response_model=JobScorecard)
def get_job_scorecard(job_id: int, db: Session = Depends(get_db)):
    job = _get_job_or_404(db, job_id)
    try:
        return scorecard_for_job(db, job)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/{job_id}/save", response_model=SavedJobRead)
def save_job(job_id: int, db: Session = Depends(get_db)):
    return set_saved_job_status(db, _get_job_or_404(db, job_id), _default_user(db), "saved")


@router.post("/{job_id}/reject", response_model=SavedJobRead)
def reject_job(job_id: int, db: Session = Depends(get_db)):
    return set_saved_job_status(db, _get_job_or_404(db, job_id), _default_user(db), "rejected")


@router.post("/{job_id}/mark-applied", response_model=SavedJobRead)
def mark_job_applied(job_id: int, db: Session = Depends(get_db)):
    job = _get_job_or_404(db, job_id)
    try:
        set_application_status(db, job, "applied")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return set_saved_job_status(db, job, _default_user(db), "applied")


@router.post("/{job_id}/mark-ready-to-apply", response_model=JobDetail)
def mark_job_ready_to_apply(job_id: int, db: Session = Depends(get_db)):
    job = _get_job_or_404(db, job_id)
    try:
        set_application_status(db, job, "ready_to_apply")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return job_detail(db, job, _default_user(db))


@router.post("/{job_id}/mark-opened", response_model=JobDetail)
def mark_job_opened(job_id: int, db: Session = Depends(get_db)):
    job = _get_job_or_404(db, job_id)
    try:
        set_application_status(db, job, "opened")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return job_detail(db, job, _default_user(db))


@router.post("/{job_id}/mark-skipped", response_model=JobDetail)
def mark_job_skipped(job_id: int, db: Session = Depends(get_db)):
    job = _get_job_or_404(db, job_id)
    try:
        set_application_status(db, job, "skipped")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return job_detail(db, job, _default_user(db))
