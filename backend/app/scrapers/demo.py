from datetime import datetime, timezone
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Job, JobSource, RawJobSnapshot, ScrapeRun
from app.scrapers.base import BaseScraper
from app.scrapers.parsers.dates import parse_posted_date
from app.scrapers.parsers.html import extract_text
from app.scrapers.parsers.json_ld import extract_job_postings
from app.scrapers.parsers.salary import parse_salary
from app.scrapers.utils.hashing import content_hash


FIXTURE_PATH = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "sample_job_listing.html"


class DemoListingParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.jobs: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._field: str | None = None
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = dict(attrs)
        classes = set((attr_map.get("class") or "").split())
        if tag == "article" and "job-card" in classes:
            self._current = {"source_job_id": attr_map.get("data-job-id", "")}
        elif self._current is not None and "job-title" in classes:
            self._start_field("title")
        elif self._current is not None and "company" in classes:
            self._start_field("company_name")
        elif self._current is not None and "location" in classes:
            self._start_field("location")
        elif self._current is not None and "salary" in classes:
            self._start_field("salary")
        elif self._current is not None and "posted" in classes:
            self._start_field("posted")
        elif self._current is not None and "description" in classes:
            self._start_field("description_text")
        elif self._current is not None and tag == "a" and "apply-link" in classes:
            self._current["canonical_url"] = attr_map.get("href", "")

    def handle_endtag(self, tag: str) -> None:
        if self._field is not None and tag in {"h2", "p", "span"}:
            self._current[self._field] = " ".join("".join(self._chunks).split())  # type: ignore[index]
            self._field = None
            self._chunks = []
        elif tag == "article" and self._current is not None:
            self.jobs.append(self._current)
            self._current = None

    def handle_data(self, data: str) -> None:
        if self._field is not None:
            self._chunks.append(data)

    def _start_field(self, field: str) -> None:
        self._field = field
        self._chunks = []


class DemoScraper(BaseScraper):
    source_name = "Demo Fixture Source"
    base_url = "https://example.invalid/jobs"
    allowed_paths = ["/jobs"]
    rate_limit_per_minute = 60
    supports_pagination = False

    def __init__(self, db: Session, fixture_path: Path = FIXTURE_PATH) -> None:
        self.db = db
        self.fixture_path = fixture_path
        self.source = self._get_or_create_source()

    def discover_job_urls(self) -> list[str]:
        html = self.fixture_path.read_text(encoding="utf-8")
        parser = DemoListingParser()
        parser.feed(html)
        return [job["canonical_url"] for job in parser.jobs if job.get("canonical_url")]

    def fetch_job(self, url: str) -> str:
        return self.fixture_path.read_text(encoding="utf-8")

    def parse_job(self, raw: str) -> dict[str, Any]:
        parser = DemoListingParser()
        parser.feed(raw)
        json_ld = extract_job_postings(raw)
        parsed_jobs = parser.jobs
        for index, job in enumerate(parsed_jobs):
            if index < len(json_ld):
                job["json_ld"] = json_ld[index]  # type: ignore[assignment]
        return {"jobs": parsed_jobs, "raw_text": extract_text(raw)}

    def normalise(self, parsed: dict[str, Any]) -> dict[str, Any]:
        normalised_jobs = []
        for item in parsed["jobs"]:
            salary = parse_salary(item.get("salary", ""))
            normalised_jobs.append(
                {
                    "source_id": self.source.id,
                    "source_job_id": item["source_job_id"],
                    "canonical_url": item["canonical_url"],
                    "title": item["title"],
                    "company_name": item.get("company_name"),
                    "location": item.get("location"),
                    "remote_type": "remote" if "remote" in item.get("location", "").lower() else None,
                    "employment_type": None,
                    "salary_min": salary["salary_min"],
                    "salary_max": salary["salary_max"],
                    "salary_currency": salary["salary_currency"],
                    "salary_min_raw": salary["salary_min_raw"],
                    "salary_max_raw": salary["salary_max_raw"],
                    "salary_period": salary["salary_period"],
                    "normalized_annual_min": salary["normalized_annual_min"],
                    "normalized_annual_max": salary["normalized_annual_max"],
                    "description_text": item.get("description_text"),
                    "posted_at": parse_posted_date(item.get("posted", "")),
                    "expires_at": None,
                    "status": "active",
                    "content_hash": content_hash(f"{item.get('title')}|{item.get('description_text')}"),
                }
            )
        return {"jobs": normalised_jobs, "raw_text": parsed["raw_text"]}

    def save_raw(self, raw: str, parsed: dict[str, Any]) -> list[RawJobSnapshot]:
        snapshots = []
        raw_hash = content_hash(raw)
        for item in parsed["jobs"]:
            snapshot = RawJobSnapshot(
                source_id=self.source.id,
                source_job_id=item.get("source_job_id"),
                url=item.get("canonical_url", self.base_url),
                raw_html=raw,
                raw_text=parsed.get("raw_text"),
                raw_json=item.get("json_ld") if isinstance(item.get("json_ld"), dict) else None,
                content_hash=raw_hash,
            )
            self.db.add(snapshot)
            snapshots.append(snapshot)
        return snapshots

    def save_clean(self, normalised: dict[str, Any]) -> tuple[int, int]:
        created = 0
        updated = 0
        for item in normalised["jobs"]:
            existing = self.db.scalar(
                select(Job).where(
                    Job.source_id == self.source.id,
                    Job.source_job_id == item["source_job_id"],
                )
            )
            if existing is None:
                self.db.add(Job(**item))
                created += 1
            else:
                for field, value in item.items():
                    setattr(existing, field, value)
                existing.last_seen_at = datetime.now(tz=timezone.utc)
                updated += 1
        return created, updated

    def run(self) -> dict[str, int | str]:
        run = ScrapeRun(source_id=self.source.id, status="running")
        self.db.add(run)
        self.db.flush()
        try:
            raw = self.fetch_job(self.base_url)
            parsed = self.parse_job(raw)
            normalised = self.normalise(parsed)
            self.save_raw(raw, parsed)
            created, updated = self.save_clean(normalised)
            run.status = "success"
            run.jobs_found = len(normalised["jobs"])
            run.jobs_created = created
            run.jobs_updated = updated
            run.finished_at = datetime.now(tz=timezone.utc)
            self.db.commit()
        except Exception as exc:
            self.db.rollback()
            run.status = "failed"
            run.error_message = str(exc)
            run.finished_at = datetime.now(tz=timezone.utc)
            self.db.add(run)
            self.db.commit()
            raise

        return {
            "scrape_run_id": run.id,
            "source_id": self.source.id,
            "status": run.status,
            "jobs_found": run.jobs_found,
            "jobs_created": run.jobs_created,
            "jobs_updated": run.jobs_updated,
        }

    def _get_or_create_source(self) -> JobSource:
        source = self.db.scalar(select(JobSource).where(JobSource.name == self.source_name))
        if source is not None:
            return source
        source = JobSource(
            name=self.source_name,
            base_url=self.base_url,
            source_type="fixture",
            robots_url="https://example.invalid/robots.txt",
            terms_url=None,
            scraping_allowed=True,
            permission_notes="Local fixture demo only; no live website is accessed.",
            rate_limit_per_minute=self.rate_limit_per_minute,
            enabled=True,
            last_reviewed_at=datetime.now(tz=timezone.utc),
        )
        self.db.add(source)
        self.db.flush()
        return source
