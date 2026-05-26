from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import User
from app.db.session import get_db
from app.schemas.database import ApplicationProfileUpdate, CVProfileCreate, UserProfileRead
from app.services.profile import get_profile, store_cv_file, update_application_profile, upsert_cv_profile

router = APIRouter(prefix="/profile", tags=["profile"])


def _default_user(db: Session) -> User:
    user = db.scalar(select(User).order_by(User.id))
    if user is None:
        user = User(email="profile@example.invalid")
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


@router.get("", response_model=UserProfileRead | None)
def read_profile(db: Session = Depends(get_db)):
    return get_profile(db, _default_user(db))


@router.post("/cv", response_model=UserProfileRead)
def save_cv_profile(payload: CVProfileCreate, db: Session = Depends(get_db)):
    return upsert_cv_profile(db, _default_user(db), payload.cv_text)


@router.post("/application", response_model=UserProfileRead)
def save_application_profile(payload: ApplicationProfileUpdate, db: Session = Depends(get_db)):
    return update_application_profile(db, _default_user(db), payload.model_dump())


@router.post("/cv-file", response_model=UserProfileRead)
def upload_cv_file(file: UploadFile = File(...), db: Session = Depends(get_db)):
    try:
        return store_cv_file(db, _default_user(db), file.filename or "cv", file.file, content_type=file.content_type)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
