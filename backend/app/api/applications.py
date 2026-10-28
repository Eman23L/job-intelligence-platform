from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.schemas.database import (
    ApplicationCreate,
    ApplicationRead,
    ApplicationsList,
    ApplicationUpdate,
    ApplyPack,
    PrepareApplicationsResult,
)
from app.services import applications as service

router = APIRouter(prefix="/applications", tags=["applications"])


def _owned(db: Session, user: User, application_id: int):
    application = service.owned_application(db, user, application_id)
    if application is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found")
    return application


@router.get("", response_model=ApplicationsList)
def get_applications(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return ApplicationsList(items=service.list_applications(db, user), minimum_apply_score=service.minimum_apply_score(db, user))


@router.post("/prepare", response_model=PrepareApplicationsResult)
def add_top_matches(
    minimum_score: int | None = Query(default=None, ge=0, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    threshold = minimum_score if minimum_score is not None else service.minimum_apply_score(db, user)
    added = service.queue_top_matches(db, user, threshold)
    return PrepareApplicationsResult(added=len(added), minimum_apply_score=threshold)


@router.post("", response_model=ApplicationRead, status_code=status.HTTP_201_CREATED)
def create_application(payload: ApplicationCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        return service.add_application(db, user, payload)
    except service.ApplicationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.patch("/{application_id}", response_model=ApplicationRead)
def update_application(
    application_id: int, payload: ApplicationUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    try:
        return service.update_application(db, user, _owned(db, user, application_id), payload)
    except service.ApplicationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.delete("/{application_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_application(application_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    service.delete_application(db, _owned(db, user, application_id))


@router.get("/{application_id}/pack", response_model=ApplyPack)
def get_apply_pack(application_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return service.apply_pack(db, user, _owned(db, user, application_id))
