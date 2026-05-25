from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status as http_status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import User
from app.db.session import get_db
from app.schemas.database import UnifiedRun, UnifiedRunList
from app.services.applications import run_prepare_applications_background
from app.services.apply_strategy import run_apply_strategy_background
from app.services.job_availability import run_availability_background
from app.services.queue import enqueue_or_background
from app.services.rescore_runs import run_rescore_background
from app.services.runs import cancel_run, list_unified_runs, retry_run

router = APIRouter(prefix="/runs", tags=["runs"])

RUN_TYPES = {"all", "scrape", "rescore", "availability", "application_prepare", "apply_strategy"}
RUN_STATUSES = {"all", "queued", "running", "completed", "failed", "stalled", "canceled", "pending"}


def _default_user(db: Session) -> User:
    user = db.scalar(select(User).order_by(User.id))
    if user is None:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail="No seeded user found")
    return user


@router.get("", response_model=UnifiedRunList)
def get_runs(
    type: str = Query(default="all"),
    status: str = Query(default="all"),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    if type not in RUN_TYPES:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail="Invalid run type")
    if status not in RUN_STATUSES:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail="Invalid run status")
    return UnifiedRunList(items=list_unified_runs(db, run_type=type, status=status, limit=limit))


@router.post("/{run_type}/{run_id}/retry", response_model=UnifiedRun)
def retry_system_run(run_type: str, run_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    if run_type not in RUN_TYPES - {"all"}:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail="Invalid run type")
    user = _default_user(db)
    result, created = retry_run(db, run_type, run_id, user)
    if result is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Run not found or cannot be retried")
    if created:
        new_id = int(result.id)
        if run_type == "rescore":
            enqueue_or_background(background_tasks, run_rescore_background, new_id, user.id, job_id=f"rescore-{new_id}")
        elif run_type == "availability":
            enqueue_or_background(background_tasks, run_availability_background, new_id, None, job_id=f"availability-{new_id}")
        elif run_type == "application_prepare":
            enqueue_or_background(background_tasks, run_prepare_applications_background, new_id, user.id, job_id=f"application-prepare-{new_id}")
        elif run_type == "apply_strategy":
            enqueue_or_background(background_tasks, run_apply_strategy_background, new_id, None, job_id=f"apply-strategy-{new_id}")
    return result


@router.post("/{run_type}/{run_id}/cancel", response_model=UnifiedRun)
def cancel_system_run(run_type: str, run_id: int, db: Session = Depends(get_db)):
    if run_type not in RUN_TYPES - {"all"}:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail="Invalid run type")
    result = cancel_run(db, run_type, run_id)
    if result is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Run not found or cannot be canceled")
    return result
