from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import User
from app.db.session import get_db
from app.schemas.database import ApplicationsList, ApplicationsPrepareResult
from app.services.applications import list_applications, prepare_applications

router = APIRouter(prefix="/applications", tags=["applications"])


def _default_user(db: Session) -> User:
    user = db.scalar(select(User).order_by(User.id))
    if user is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No seeded user found")
    return user


@router.get("", response_model=ApplicationsList)
def get_applications(db: Session = Depends(get_db)):
    return ApplicationsList(items=list_applications(db, _default_user(db)))


@router.post("/prepare", response_model=ApplicationsPrepareResult)
def prepare_application_queue(db: Session = Depends(get_db)):
    queued, job_ids = prepare_applications(db, _default_user(db))
    return ApplicationsPrepareResult(queued=queued, job_ids=job_ids)
