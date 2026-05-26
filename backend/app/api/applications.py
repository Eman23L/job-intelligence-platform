from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Job, User
from app.db.session import get_db
from app.schemas.database import ApplicationPrepareRunStart, ApplicationPrepareRunStatus, ApplicationsList, AssistApplyRequest, AssistApplyResult
from app.services.apply_agent import BrowserAutomationError, assist_apply_application
from app.services.applications import (
    get_prepare_applications_run_status,
    list_applications,
    minimum_apply_score,
    run_prepare_applications_background,
    start_prepare_applications_run,
)
from app.services.queue import enqueue_or_background

router = APIRouter(prefix="/applications", tags=["applications"])


def _default_user(db: Session) -> User:
    user = db.scalar(select(User).order_by(User.id))
    if user is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No seeded user found")
    return user


@router.get("", response_model=ApplicationsList)
def get_applications(db: Session = Depends(get_db)):
    user = _default_user(db)
    return ApplicationsList(items=list_applications(db, user), minimum_apply_score=minimum_apply_score(db, user))


@router.post("/prepare", response_model=ApplicationPrepareRunStart)
def prepare_application_queue(background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    user = _default_user(db)
    started, created = start_prepare_applications_run(db, user)
    if created:
        enqueue_or_background(background_tasks, run_prepare_applications_background, started.run_id, user.id, job_id=f"application-prepare-{started.run_id}")
    return started


@router.get("/prepare-runs/{run_id}", response_model=ApplicationPrepareRunStatus)
def get_prepare_application_run(run_id: int, db: Session = Depends(get_db)):
    result = get_prepare_applications_run_status(db, run_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prepare run not found")
    return result


@router.post("/{application_id}/assist-apply", response_model=AssistApplyResult)
def assist_apply(application_id: int, payload: AssistApplyRequest | None = None, db: Session = Depends(get_db)):
    job = db.get(Job, application_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found")
    try:
        return assist_apply_application(db, job, _default_user(db), mode=(payload.mode if payload else "review_only"))
    except BrowserAutomationError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail={"error": exc.error, "message": exc.message}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
