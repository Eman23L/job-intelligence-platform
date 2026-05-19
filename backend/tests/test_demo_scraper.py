from sqlalchemy import select

from app.db.models import Job, RawJobSnapshot, ScrapeRun
from app.scrapers.demo import DemoScraper


def test_demo_scraper_saves_raw_snapshots_and_jobs(db_session) -> None:
    result = DemoScraper(db_session).run()

    snapshots = db_session.scalars(select(RawJobSnapshot)).all()
    jobs = db_session.scalars(select(Job)).all()
    runs = db_session.scalars(select(ScrapeRun)).all()

    assert result["status"] == "success"
    assert result["jobs_found"] == 2
    assert len(snapshots) == 2
    assert len(jobs) == 2
    assert len(runs) == 1
    assert jobs[0].title in {"Data Analyst", "Junior BI Developer"}
