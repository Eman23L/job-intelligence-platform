from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Job, SavedJob, User
from app.schemas.database import SavedJobUpdate

VALID_STATUSES = {"saved", "rejected", "applied", "interviewing", "offer", "closed"}


def set_saved_job_status(db: Session, job: Job, user: User, status: str) -> SavedJob:
    if status not in VALID_STATUSES:
        raise ValueError("Invalid saved job status")
    saved = db.scalar(select(SavedJob).where(SavedJob.job_id == job.id, SavedJob.user_id == user.id))
    if saved is None:
        saved = SavedJob(job_id=job.id, user_id=user.id, status=status)
        db.add(saved)
    else:
        saved.status = status
    db.commit()
    db.refresh(saved)
    return saved


def update_saved_job(db: Session, saved: SavedJob, payload: SavedJobUpdate) -> SavedJob:
    data = payload.model_dump(exclude_unset=True)
    if "status" in data and data["status"] not in VALID_STATUSES:
        raise ValueError("Invalid saved job status")
    for field, value in data.items():
        setattr(saved, field, value)
    db.commit()
    db.refresh(saved)
    return saved


def list_saved_jobs(db: Session, user: User | None = None, status: str | None = None) -> list[SavedJob]:
    query = select(SavedJob).order_by(SavedJob.saved_at.desc())
    if user is not None:
        query = query.where(SavedJob.user_id == user.id)
    if status is not None:
        query = query.where(SavedJob.status == status)
    return list(db.scalars(query).all())
