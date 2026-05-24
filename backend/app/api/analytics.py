import logging
from time import perf_counter

from fastapi import APIRouter, Depends
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.database import (
    AnalyticsOverview,
    RoleFitAnalytics,
    SalaryAnalytics,
    SkillGapAnalytics,
    SourceHealthAnalytics,
)
from app.services import analytics as analytics_service

router = APIRouter(prefix="/analytics", tags=["analytics"])
logger = logging.getLogger(__name__)


@router.get("/overview", response_model=AnalyticsOverview)
def get_overview(db: Session = Depends(get_db)):
    started_at = perf_counter()
    result = analytics_service.overview(db)
    duration_ms = (perf_counter() - started_at) * 1000
    logger.info(
        "analytics.overview completed duration_ms=%.2f total_jobs=%s scored_jobs=%s",
        duration_ms,
        result.total_jobs,
        result.scored_jobs,
    )
    return result


@router.get("/role-fit", response_model=RoleFitAnalytics)
def get_role_fit(db: Session = Depends(get_db)):
    return analytics_service.role_fit(db)


@router.get("/skill-gaps", response_model=SkillGapAnalytics)
def get_skill_gaps(db: Session = Depends(get_db)):
    return analytics_service.skill_gaps(db)


@router.get("/salary", response_model=SalaryAnalytics)
def get_salary(db: Session = Depends(get_db)):
    return analytics_service.salary(db)


@router.get("/source-health", response_model=SourceHealthAnalytics)
def get_source_health(db: Session = Depends(get_db)):
    started_at = perf_counter()
    query_count = 0

    def count_query(*args):
        nonlocal query_count
        query_count += 1

    bind = db.get_bind()
    if isinstance(bind, Engine):
        event.listen(bind, "before_cursor_execute", count_query)
    try:
        result = analytics_service.source_health(db)
        duration_ms = (perf_counter() - started_at) * 1000
        logger.info(
            "analytics.source_health completed count=%s duration_ms=%.2f query_count=%s",
            len(result.items),
            duration_ms,
            query_count,
        )
        return result
    except Exception:
        logger.exception("analytics.source_health failed")
        raise
    finally:
        if isinstance(bind, Engine):
            event.remove(bind, "before_cursor_execute", count_query)
