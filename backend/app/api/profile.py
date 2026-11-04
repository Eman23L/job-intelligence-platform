import logging

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.schemas.database import (
    ApplicationProfileUpdate,
    CvNotesUpdate,
    CVProfileCreate,
    CvRefresh,
    CvUpdatesRead,
    SearchPreferencesUpdate,
    UserProfileRead,
)
from app.services import cv_updates
from app.services.profile import get_profile, store_cv_file, update_application_profile, update_search_preferences
from app.services.queue import enqueue_or_background
from app.services.rescore_runs import run_rescore_background, start_rescore_run

router = APIRouter(prefix="/profile", tags=["profile"])
logger = logging.getLogger(__name__)


@router.get("", response_model=UserProfileRead | None)
def read_profile(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return get_profile(db, user)


@router.post("/cv", response_model=UserProfileRead)
def save_cv_profile(payload: CVProfileCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return cv_updates.save_cv_text(db, user, payload.cv_text)


@router.get("/cv/updates", response_model=CvUpdatesRead)
def read_cv_updates(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """When Claude last checked the CV, whether a refresh is due, notes for it, and earlier versions."""
    return cv_updates.refresh_status(db, user)


@router.post("/cv/notes", response_model=CvUpdatesRead)
def save_cv_notes(payload: CvNotesUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        cv_updates.save_notes(db, user, payload.notes)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return cv_updates.refresh_status(db, user)


@router.post("/cv/refresh", response_model=CvUpdatesRead)
def refresh_cv(
    payload: CvRefresh, background_tasks: BackgroundTasks, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """Claude saves the updated CV (or, with no text, records that nothing was new). The PDF is rebuilt to match."""
    try:
        _, changed = cv_updates.refresh_cv(db, user, payload.cv_text, payload.changes)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if changed:
        _rescore(background_tasks, db, user)
    return cv_updates.refresh_status(db, user)


@router.post("/cv/versions/{version_id}/restore", response_model=UserProfileRead)
def restore_cv_version(
    version_id: int, background_tasks: BackgroundTasks, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    try:
        profile = cv_updates.restore_version(db, user, version_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    _rescore(background_tasks, db, user)
    return profile


@router.post("/cv-file/from-text", response_model=UserProfileRead)
def make_cv_pdf(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Replace the attached CV file with a PDF made from the CV text."""
    try:
        return cv_updates.rebuild_pdf(db, user)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


def _rescore(background_tasks: BackgroundTasks, db: Session, user: User) -> None:
    # Match scores depend on the CV, so bring them up to date. A failure here mustn't undo the CV change.
    try:
        started, created = start_rescore_run(db, user)
        if created:
            enqueue_or_background(background_tasks, run_rescore_background, started.run_id, user.id, job_id=f"rescore-{started.run_id}")
    except Exception:  # noqa: BLE001
        logger.exception("cv_rescore_failed user_id=%s", user.id)


@router.post("/application", response_model=UserProfileRead)
def save_application_profile(payload: ApplicationProfileUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return update_application_profile(db, user, payload.model_dump())


@router.post("/search", response_model=UserProfileRead)
def save_search_preferences(payload: SearchPreferencesUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """The roles and place the daily search looks for, even ones the CV doesn't show yet."""
    return update_search_preferences(
        db,
        user,
        roles=payload.roles,
        location=payload.location,
        remote_only=payload.remote_only,
        auto_apply_min_score=payload.auto_apply_min_score,
    )


@router.post("/cv-file", response_model=UserProfileRead)
def upload_cv_file(file: UploadFile = File(...), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        return store_cv_file(db, user, file.filename or "cv", file.file, content_type=file.content_type)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/cv-file")
def download_cv_file(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    profile = get_profile(db, user)
    if profile is None or not profile.cv_file_bytes:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No CV file uploaded yet")
    filename = (profile.cv_file_name or "cv").replace('"', "")
    return Response(
        content=profile.cv_file_bytes,
        media_type=profile.cv_file_mime_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
