import logging

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.database import (
    JobSourceCreate,
    JobSourceRead,
    JobSourceUpdate,
    ScrapeNowRequest,
    ScrapeStartResult,
    SourceFromUrlCreate,
    SourcePermissionValidation,
    SourceTestResult,
)
from app.scrapers.demo import DemoScraper
from app.services import source_scraping
from app.services import sources as source_service

router = APIRouter(prefix="/sources", tags=["sources"])
logger = logging.getLogger(__name__)


def _get_source_or_404(db: Session, source_id: int):
    source = source_service.get_source(db, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    return source


@router.get("", response_model=list[JobSourceRead])
def list_sources(db: Session = Depends(get_db)):
    try:
        sources = source_service.list_sources(db)
        logger.info("sources.list completed count=%s", len(sources))
        return sources
    except Exception:
        logger.exception("sources.list failed")
        raise


@router.post("/demo-scrape")
def run_demo_scrape(db: Session = Depends(get_db)):
    result = DemoScraper(db).run()
    return result


@router.post("/from-url", response_model=JobSourceRead, status_code=status.HTTP_201_CREATED)
def create_source_from_url(payload: SourceFromUrlCreate, db: Session = Depends(get_db)):
    try:
        return source_scraping.create_source_from_url(db, payload)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Source already exists") from exc


@router.get("/{source_id}", response_model=JobSourceRead)
def get_source(source_id: int, db: Session = Depends(get_db)):
    return _get_source_or_404(db, source_id)


@router.post("", response_model=JobSourceRead, status_code=status.HTTP_201_CREATED)
def create_source(payload: JobSourceCreate, db: Session = Depends(get_db)):
    try:
        return source_service.create_source(db, payload)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Source already exists") from exc


@router.patch("/{source_id}", response_model=JobSourceRead)
def update_source(source_id: int, payload: JobSourceUpdate, db: Session = Depends(get_db)):
    source = _get_source_or_404(db, source_id)
    try:
        return source_service.update_source(db, source, payload)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Source update conflicts") from exc


@router.post("/{source_id}/enable", response_model=JobSourceRead)
def enable_source(source_id: int, db: Session = Depends(get_db)):
    source = _get_source_or_404(db, source_id)
    return source_service.set_source_enabled(db, source, True)


@router.post("/{source_id}/disable", response_model=JobSourceRead)
def disable_source(source_id: int, db: Session = Depends(get_db)):
    source = _get_source_or_404(db, source_id)
    return source_service.set_source_enabled(db, source, False)


@router.post("/{source_id}/validate-permission", response_model=SourcePermissionValidation)
def validate_permission(source_id: int, db: Session = Depends(get_db)):
    source = _get_source_or_404(db, source_id)
    return source_service.validate_source_permission(source)


@router.post("/{source_id}/test-url", response_model=SourceTestResult)
def test_source_url(
    source_id: int,
    target_url: str | None = Body(default=None, embed=True),
    db: Session = Depends(get_db),
):
    source = _get_source_or_404(db, source_id)
    return source_scraping.test_source_url(db, source, target_url)


@router.post("/{source_id}/scrape-now", response_model=ScrapeStartResult)
def scrape_source_now(
    source_id: int,
    background_tasks: BackgroundTasks,
    payload: ScrapeNowRequest | None = None,
    db: Session = Depends(get_db),
):
    source = _get_source_or_404(db, source_id)
    request_payload = payload or ScrapeNowRequest()
    started = source_scraping.start_scrape_source_now(db, source, request_payload)
    background_tasks.add_task(
        source_scraping.run_scrape_background,
        started.scrape_run_id,
        source.id,
        request_payload.model_dump(),
    )
    return started
