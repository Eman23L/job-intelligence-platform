from fastapi import APIRouter, Depends
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


@router.get("/overview", response_model=AnalyticsOverview)
def get_overview(db: Session = Depends(get_db)):
    return analytics_service.overview(db)


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
    return analytics_service.source_health(db)
