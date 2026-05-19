from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Job, JobAnalysis, JobScore, JobSkill, User
from app.db.session import get_db
from app.schemas.database import JobAnalysisRead, JobDetail, JobScoreRead, JobSkillRead, PaginatedJobs, SavedJobRead
from app.services.analysis import analyse_all_jobs, analyse_job
from app.services.job_discovery import JobFilters, job_detail, list_jobs
from app.services.saved_jobs import set_saved_job_status
from app.services.scoring import score_all_jobs, score_job

router = APIRouter(prefix="/jobs", tags=["jobs"])


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
    )
    return list_jobs(db, filters, sort=sort, page=page, page_size=page_size)


@router.get("/{job_id}", response_model=JobDetail)
def get_job_detail(job_id: int, db: Session = Depends(get_db)):
    return job_detail(db, _get_job_or_404(db, job_id))


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


@router.post("/{job_id}/save", response_model=SavedJobRead)
def save_job(job_id: int, db: Session = Depends(get_db)):
    return set_saved_job_status(db, _get_job_or_404(db, job_id), _default_user(db), "saved")


@router.post("/{job_id}/reject", response_model=SavedJobRead)
def reject_job(job_id: int, db: Session = Depends(get_db)):
    return set_saved_job_status(db, _get_job_or_404(db, job_id), _default_user(db), "rejected")


@router.post("/{job_id}/mark-applied", response_model=SavedJobRead)
def mark_job_applied(job_id: int, db: Session = Depends(get_db)):
    return set_saved_job_status(db, _get_job_or_404(db, job_id), _default_user(db), "applied")
