from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.database import JobScoreRead
from app.services.scoring import missing_skills_summary, recommendations, top_scores

router = APIRouter(prefix="/scores", tags=["scores"])


@router.get("/top", response_model=list[JobScoreRead])
def get_top_scores(limit: int = 10, db: Session = Depends(get_db)):
    return top_scores(db, limit=limit)


@router.get("/missing-skills")
def get_missing_skills(db: Session = Depends(get_db)):
    return missing_skills_summary(db)


@router.get("/recommendations", response_model=list[JobScoreRead])
def get_recommendations(limit: int = 20, db: Session = Depends(get_db)):
    return recommendations(db, limit=limit)
