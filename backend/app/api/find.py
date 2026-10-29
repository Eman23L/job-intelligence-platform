from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.schemas.database import FindActionResult, FindResult, JobIdList
from app.services.applications import dismiss_jobs, queue_jobs
from app.services.find import find_jobs

router = APIRouter(prefix="/find", tags=["find"])


@router.get("", response_model=FindResult)
def get_found_jobs(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return find_jobs(db, user)


@router.post("/apply", response_model=FindActionResult)
def apply_to_jobs(payload: JobIdList, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Queue jobs for Claude to apply to."""
    return FindActionResult(changed=queue_jobs(db, user, payload.job_ids))


@router.post("/dismiss", response_model=FindActionResult)
def dismiss_found_jobs(payload: JobIdList, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Hide jobs that aren't a fit."""
    return FindActionResult(changed=dismiss_jobs(db, user, payload.job_ids))
