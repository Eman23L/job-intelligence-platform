from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.schemas.database import ApplicationProfileUpdate, CVProfileCreate, UserProfileRead
from app.services.profile import get_profile, store_cv_file, update_application_profile, upsert_cv_profile

router = APIRouter(prefix="/profile", tags=["profile"])


@router.get("", response_model=UserProfileRead | None)
def read_profile(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return get_profile(db, user)


@router.post("/cv", response_model=UserProfileRead)
def save_cv_profile(payload: CVProfileCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return upsert_cv_profile(db, user, payload.cv_text)


@router.post("/application", response_model=UserProfileRead)
def save_application_profile(payload: ApplicationProfileUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return update_application_profile(db, user, payload.model_dump())


@router.post("/cv-file", response_model=UserProfileRead)
def upload_cv_file(file: UploadFile = File(...), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        return store_cv_file(db, user, file.filename or "cv", file.file, content_type=file.content_type)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
