from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.services.discovery import run_discovery, searches_for
from app.services.profile import get_profile
from app.services.queue import enqueue_or_background

router = APIRouter(prefix="/discovery", tags=["discovery"])


@router.post("/run", status_code=status.HTTP_202_ACCEPTED)
def run_now(background_tasks: BackgroundTasks, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Run a new search now for the roles this person wants."""
    profile = get_profile(db, user)
    if not profile or not profile.cv_text or not searches_for(profile):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Add your CV and the jobs you want first.")
    # Mark it as started straight away so the page shows "Searching" before the worker picks it up.
    profile.search_started_at = datetime.now(tz=timezone.utc)
    db.commit()
    stamp = profile.search_started_at.strftime("%Y%m%d%H%M%S")
    enqueue_or_background(background_tasks, run_discovery, [user.id], job_id=f"discovery-{user.id}-{stamp}", job_timeout=1800)
    return {"status": "started"}
