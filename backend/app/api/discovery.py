from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import JobSource, ScrapeRun, User
from app.db.session import get_db
from app.schemas.database import DiscoveryFeed, DiscoveryStatus, ImportKeyStatus
from app.services.applications import minimum_apply_score
from app.services.discovery import FEED_SOURCES, feeds_configured, next_run_time, run_discovery, searches_for
from app.services.profile import get_profile
from app.services.queue import enqueue_or_background, queue_enabled
from app.services.source_scraping import JOBSERVE_SEARCH_SOURCE_NAME

router = APIRouter(prefix="/discovery", tags=["discovery"])

FEED_NAMES = {"jobserve": ("JobServe", JOBSERVE_SEARCH_SOURCE_NAME), **{key: (name, name) for key, (name, _url, _notes) in FEED_SOURCES.items()}}


@router.get("/status", response_model=DiscoveryStatus)
def discovery_status(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    profile = get_profile(db, user)
    searches = searches_for(profile) if profile and profile.cv_text else []
    configured = feeds_configured()
    feeds = []
    for key, (label, source_name) in FEED_NAMES.items():
        source = db.scalar(select(JobSource).where(JobSource.name == source_name))
        last = (
            db.scalar(select(ScrapeRun).where(ScrapeRun.source_id == source.id).order_by(ScrapeRun.started_at.desc()).limit(1))
            if source
            else None
        )
        feeds.append(
            DiscoveryFeed(
                key=key,
                name=label,
                enabled=configured[key],
                last_run_at=last.started_at if last else None,
                last_status=last.status if last else None,
                last_found=last.jobs_found if last else 0,
                last_created=last.jobs_created if last else 0,
            )
        )
    return DiscoveryStatus(
        roles=[search.role for search in searches],
        location=searches[0].location if searches else None,
        minimum_score=minimum_apply_score(db, user),
        feeds=feeds,
        next_run_at=next_run_time() if queue_enabled() else None,
        import_key=ImportKeyStatus(
            exists=bool(user.import_key_hash), created_at=user.import_key_created_at, last_used_at=user.import_key_last_used_at
        ),
    )


@router.post("/run", status_code=status.HTTP_202_ACCEPTED)
def run_now(background_tasks: BackgroundTasks, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Run the daily search now for this person's target roles."""
    profile = get_profile(db, user)
    if not profile or not profile.cv_text or not searches_for(profile):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Add your CV with your target roles on the Profile page first.")
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d%H%M%S")
    enqueue_or_background(background_tasks, run_discovery, [user.id], job_id=f"discovery-{user.id}-{stamp}", job_timeout=1800)
    return {"status": "started"}
