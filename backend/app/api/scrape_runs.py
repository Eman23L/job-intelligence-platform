from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.database import ScrapeRunStatus
from app.services.source_scraping import get_scrape_run_status


router = APIRouter(prefix="/scrape-runs", tags=["scrape-runs"])


@router.get("/{scrape_run_id}", response_model=ScrapeRunStatus)
def scrape_run_status(scrape_run_id: int, db: Session = Depends(get_db)):
    result = get_scrape_run_status(db, scrape_run_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scrape run not found")
    return result
