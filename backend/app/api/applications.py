from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import User
from app.db.session import get_db
from app.schemas.database import ApplicationPrepareRunStart, ApplicationPrepareRunStatus, ApplicationsList
from app.services.applications import (
    get_prepare_applications_run_status,
    list_applications,
    run_prepare_applications_background,
    start_prepare_applications_run,
)

router = APIRouter(prefix="/applications", tags=["applications"])


def _default_user(db: Session) -> User:
    user = db.scalar(select(User).order_by(User.id))
    if user is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No seeded user found")
    return user


@router.get("", response_model=ApplicationsList)
def get_applications(db: Session = Depends(get_db)):
    return ApplicationsList(items=list_applications(db, _default_user(db)))


@router.post("/prepare", response_model=ApplicationPrepareRunStart)
def prepare_application_queue(background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    user = _default_user(db)
    started, created = start_prepare_applications_run(db, user)
    if created:
        background_tasks.add_task(run_prepare_applications_background, started.run_id, user.id)
    return started


@router.get("/prepare-runs/{run_id}", response_model=ApplicationPrepareRunStatus)
def get_prepare_application_run(run_id: int, db: Session = Depends(get_db)):
    result = get_prepare_applications_run_status(db, run_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prepare run not found")
    return result
