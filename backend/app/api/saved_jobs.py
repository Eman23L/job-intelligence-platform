from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import SavedJob, User
from app.db.session import get_db
from app.schemas.database import SavedJobListItem, SavedJobRead, SavedJobUpdate
from app.services.job_discovery import JobFilters, list_jobs
from app.services.saved_jobs import list_saved_jobs, update_saved_job

router = APIRouter(prefix="/saved-jobs", tags=["saved-jobs"])


def _default_user(db: Session) -> User:
    user = db.scalar(select(User).order_by(User.id))
    if user is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No seeded user found")
    return user


@router.patch("/{saved_job_id}", response_model=SavedJobRead)
def patch_saved_job(saved_job_id: int, payload: SavedJobUpdate, db: Session = Depends(get_db)):
    saved = db.get(SavedJob, saved_job_id)
    if saved is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Saved job not found")
    try:
        return update_saved_job(db, saved, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("", response_model=list[SavedJobListItem])
def get_saved_jobs(status_filter: str | None = None, db: Session = Depends(get_db)):
    user = _default_user(db)
    saved_rows = list_saved_jobs(db, user=user, status=status_filter)
    job_items = {item.id: item for item in list_jobs(db, JobFilters(), page_size=100, user=user).items}
    return [
        SavedJobListItem(
            id=saved.id,
            user_id=saved.user_id,
            job_id=saved.job_id,
            status=saved.status,
            notes=saved.notes,
            saved_at=saved.saved_at,
            job=job_items.get(saved.job_id),
        )
        for saved in saved_rows
    ]
