from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.schemas.database import ImportKeyCreated, ImportKeyStatus, SuggestionImport, SuggestionImportResult
from app.services.applications import import_suggestions
from app.services.auth import create_import_key, revoke_import_key, user_for_import_key

# Public route: authenticated by the person's import key, not a sign-in session.
import_router = APIRouter(prefix="/suggestions", tags=["suggestions"])
router = APIRouter(prefix="/suggestions", tags=["suggestions"])


@import_router.post("/import", response_model=SuggestionImportResult)
def import_jobs(payload: SuggestionImport, x_import_key: str = Header(default=""), db: Session = Depends(get_db)):
    """Add jobs found in LinkedIn and Indeed alert emails to the key owner's New matches."""
    user = user_for_import_key(db, x_import_key) if x_import_key else None
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid import key.")
    added, skipped = import_suggestions(db, user, [item.model_dump() for item in payload.jobs])
    return SuggestionImportResult(added=added, skipped=skipped)


@router.get("/key", response_model=ImportKeyStatus)
def get_import_key(user: User = Depends(get_current_user)):
    return _key_status(user)


@router.post("/key", response_model=ImportKeyCreated)
def new_import_key(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    key = create_import_key(db, user)
    return ImportKeyCreated(key=key, **_key_status(user).model_dump())


@router.delete("/key", status_code=status.HTTP_204_NO_CONTENT)
def delete_import_key(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    revoke_import_key(db, user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _key_status(user: User) -> ImportKeyStatus:
    return ImportKeyStatus(
        exists=bool(user.import_key_hash), created_at=user.import_key_created_at, last_used_at=user.import_key_last_used_at
    )
