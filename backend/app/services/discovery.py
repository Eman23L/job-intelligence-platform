"""Daily job discovery: search the job boards for each person's target roles and suggest the best new matches.

Runs on the worker every weekday morning. New jobs are saved, analysed and scored against everyone's CV, and
those that score at or above a person's minimum apply score land in their New matches for approval.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time as day_time, timedelta, timezone
from decimal import Decimal, InvalidOperation
import logging
import re
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import Job, JobAnalysis, JobSource, ScrapeRun, User, UserProfile
from app.db.session import SessionLocal
from app.schemas.database import JobServeSearchScrapeRequest
from app.scrapers.parsers.html import extract_text
from app.scrapers.utils.hashing import content_hash
from app.services import source_scraping
from app.services.analysis import analyse_job
from app.services.applications import suggest_new_matches
from app.services.job_validation import validate_normalised_job
from app.services.normalisation import normalise_job_fields

LOGGER = logging.getLogger(__name__)

RUN_AT = day_time(6, 30)
MAX_ROLES = 5
RESULTS_PER_SEARCH = 25
# Jobs first seen this recently can still be suggested, so a run started by one person reaches everyone.
SUGGEST_WITHIN_DAYS = 3
REQUEST_TIMEOUT_SECONDS = 20
USER_AGENT = "JobSearch/1.0 (+https://jobs.thetechbuilder.co.uk)"

REED_SEARCH_URL = "https://www.reed.co.uk/api/1.0/search"
REED_DETAIL_URL = "https://www.reed.co.uk/api/1.0/jobs/{job_id}"
ADZUNA_SEARCH_URL = "https://api.adzuna.com/v1/api/jobs/gb/search/1"


@dataclass(frozen=True)
class Search:
    role: str
    location: str | None


@dataclass
class FeedResult:
    found: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


def london_time_zone():
    try:
        return ZoneInfo("Europe/London")
    except ZoneInfoNotFoundError:
        return timezone.utc


def next_run_time(now: datetime | None = None) -> datetime:
    """The next weekday at 06:30 UK time."""
    zone = london_time_zone()
    current = (now or datetime.now(tz=timezone.utc)).astimezone(zone)
    candidate = datetime.combine(current.date(), RUN_AT, tzinfo=zone)
    if candidate <= current:
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


def feeds_configured() -> dict[str, bool]:
    return {
        "jobserve": True,
        "reed": bool(settings.reed_api_key),
        "adzuna": bool(settings.adzuna_app_id and settings.adzuna_app_key),
    }


def searches_for(profile: UserProfile) -> list[Search]:
    roles = [role.strip() for role in (profile.preferred_roles or []) if role and role.strip()][:MAX_ROLES]
    location = (profile.location_preference or "").split("/")[0].strip() or None
    return [Search(role=role, location=location) for role in roles]


def schedule_next_run(queue) -> datetime:
    """Queue tomorrow's (or today's) run once; a fixed job id per day stops duplicates across worker restarts."""
    from app.services.queue import call_by_path

    run_at = next_run_time()
    job_id = f"daily-discovery-{run_at:%Y%m%d}"
    if queue.fetch_job(job_id) is None:
        queue.enqueue_at(
            run_at,
            call_by_path,
            "app.services.discovery.run_scheduled_discovery",
            job_id=job_id,
            job_timeout=3600,
        )
        LOGGER.info("daily_discovery_scheduled run_at=%s job_id=%s", run_at.isoformat(), job_id)
    return run_at


def run_scheduled_discovery() -> dict[str, Any]:
    # Schedule the next run first, so one failed morning doesn't stop the daily searches.
    try:
        from app.services.queue import rq_queue

        schedule_next_run(rq_queue())
    except Exception:  # noqa: BLE001
        LOGGER.exception("daily_discovery_reschedule_failed")
    return run_discovery()


def run_discovery(user_ids: list[int] | None = None) -> dict[str, Any]:
    """Search every feed for the target roles of everyone with a CV (or just `user_ids`), then suggest new matches."""
    with SessionLocal() as db:
        return discover(db, user_ids=user_ids)


SearchOne = Callable[[Session, JobSource, Search], "FeedResult"]


def default_feeds() -> dict[str, SearchOne]:
    configured = feeds_configured()
    feeds: dict[str, SearchOne] = {"jobserve": _search_jobserve}
    if configured["reed"]:
        feeds["reed"] = lambda db, source, search: _save_records(db, source, fetch_reed(search))
    if configured["adzuna"]:
        feeds["adzuna"] = lambda db, source, search: _save_records(db, source, fetch_adzuna(search))
    return feeds


def discover(db: Session, *, user_ids: list[int] | None = None, feeds: dict[str, SearchOne] | None = None) -> dict[str, Any]:
    profiles_query = select(UserProfile).where(UserProfile.cv_text != "")
    if user_ids:
        profiles_query = profiles_query.where(UserProfile.user_id.in_(user_ids))
    searches: list[Search] = []
    for profile in db.scalars(profiles_query.order_by(UserProfile.user_id)).all():
        for search in searches_for(profile):
            if search not in searches:
                searches.append(search)
    LOGGER.info("daily_discovery_start searches=%s", [(s.role, s.location) for s in searches])

    summary: dict[str, Any] = {"searches": len(searches), "feeds": {}}
    if searches:
        for key, search_one in (feeds if feeds is not None else default_feeds()).items():
            summary["feeds"][key] = _run_feed(db, key, searches, search_one)

    _analyse_new_jobs(db)
    source_scraping.score_new_jobs_for_everyone(db)
    summary["suggested"] = suggest_for_everyone(db)
    LOGGER.info("daily_discovery_finished summary=%s", summary)
    return summary


def suggest_for_everyone(db: Session) -> dict[int, int]:
    since = datetime.now(tz=timezone.utc) - timedelta(days=SUGGEST_WITHIN_DAYS)
    recent = list(db.scalars(select(Job.id).where(Job.first_seen_at >= since)).all())
    added: dict[int, int] = {}
    for profile in db.scalars(select(UserProfile).where(UserProfile.cv_text != "")).all():
        user = db.get(User, profile.user_id)
        if user is not None:
            added[user.id] = suggest_new_matches(db, user, recent)
    return added


def _analyse_new_jobs(db: Session) -> None:
    # Skills come from the analysis; without it every job looks like a perfect skill match.
    since = datetime.now(tz=timezone.utc) - timedelta(days=SUGGEST_WITHIN_DAYS)
    analysed = select(JobAnalysis.job_id)
    for job in db.scalars(select(Job).where(Job.first_seen_at >= since, Job.id.not_in(analysed))).all():
        try:
            analyse_job(db, job)
        except Exception:  # noqa: BLE001
            db.rollback()
            LOGGER.exception("daily_discovery_analysis_failed job_id=%s", job.id)


def _run_feed(db: Session, key: str, searches: list[Search], search_one: SearchOne) -> dict[str, Any]:
    source = _feed_source(db, key)
    run = ScrapeRun(source_id=source.id, status="running", started_at=datetime.now(tz=timezone.utc), errors=[], parsed_jobs=[])
    db.add(run)
    db.commit()
    total = FeedResult()
    for search in searches:
        try:
            result = search_one(db, source, search)
        except Exception as exc:  # noqa: BLE001 - one failed search shouldn't stop the others.
            db.rollback()
            LOGGER.exception("daily_discovery_search_failed feed=%s role=%s", key, search.role)
            result = FeedResult(errors=[f"{search.role}: {exc}"])
        total.found += result.found
        total.created += result.created
        total.updated += result.updated
        total.skipped += result.skipped
        total.errors.extend(result.errors)
    run = db.get(ScrapeRun, run.id)
    run.status = "failed" if total.errors and not total.found else "completed"
    run.finished_at = datetime.now(tz=timezone.utc)
    run.jobs_found, run.jobs_created, run.jobs_updated, run.jobs_skipped = total.found, total.created, total.updated, total.skipped
    run.errors = total.errors[:20]
    run.error_message = "; ".join(total.errors[:3]) or None
    run.parsed_jobs = [{"search_metadata": {"daily_search": True, "searches": [s.role for s in searches]}}]
    db.commit()
    return {"found": total.found, "created": total.created, "errors": len(total.errors)}


FEED_SOURCES = {
    "reed": ("Reed", "https://www.reed.co.uk", "Official Reed jobseeker API."),
    "adzuna": ("Adzuna", "https://www.adzuna.co.uk", "Official Adzuna jobs API."),
}


def _feed_source(db: Session, key: str) -> JobSource:
    if key == "jobserve":
        return source_scraping._get_or_create_jobserve_search_source(db)
    name, base_url, notes = FEED_SOURCES[key]
    source = db.scalar(select(JobSource).where(JobSource.name == name))
    if source is None:
        source = JobSource(
            name=name,
            base_url=base_url,
            source_type="api",
            scraping_allowed=True,
            permission_notes=notes,
            rate_limit_per_minute=60,
            enabled=True,
            last_reviewed_at=datetime.now(tz=timezone.utc),
        )
        db.add(source)
        db.flush()
    return source


def _search_jobserve(db: Session, source: JobSource, search: Search) -> FeedResult:
    payload = JobServeSearchScrapeRequest(keywords=search.role[:120], location=search.location, max_pages=1)
    result = source_scraping.search_scrape_jobserve(db, payload)
    return FeedResult(
        found=result.jobs_found, created=result.jobs_created, updated=result.jobs_updated, skipped=result.jobs_skipped, errors=result.errors
    )


def _save_records(db: Session, source: JobSource, records: list[dict[str, Any]]) -> FeedResult:
    result = FeedResult(found=len(records))
    for record in records:
        item = _normalised(source, record)
        validation = validate_normalised_job(item, source_name=source.name)
        if not validation.is_valid:
            result.skipped += 1
            continue
        if source_scraping._upsert_job(db, source, item):
            result.created += 1
        else:
            result.updated += 1
    db.commit()
    return result


def _normalised(source: JobSource, record: dict[str, Any]) -> dict[str, Any]:
    description = record.get("description") or ""
    normalised = normalise_job_fields(
        title=record["title"],
        company_name=record.get("company"),
        location=record.get("location"),
        employment_type=record.get("employment_type"),
        salary_min=record.get("salary_min"),
        salary_max=record.get("salary_max"),
        salary_currency="GBP" if record.get("salary_min") or record.get("salary_max") else None,
        salary_period="year" if record.get("salary_min") or record.get("salary_max") else None,
        posted_date=record.get("posted_at"),
        canonical_url=record["url"],
        description_text=description,
    )
    return {
        "source_id": source.id,
        "source_job_id": str(record["id"]),
        "canonical_url": normalised.canonical_url,
        "title": normalised.title,
        "company_name": normalised.company_name,
        "location": normalised.location,
        "remote_type": normalised.remote_type,
        "employment_type": normalised.employment_type,
        "salary_min": normalised.salary_min,
        "salary_max": normalised.salary_max,
        "salary_currency": normalised.salary_currency,
        "salary_min_raw": normalised.salary_min_raw,
        "salary_max_raw": normalised.salary_max_raw,
        "salary_period": normalised.salary_period,
        "normalized_annual_min": normalised.normalized_annual_min,
        "normalized_annual_max": normalised.normalized_annual_max,
        "description_text": description,
        "posted_at": normalised.posted_at,
        "expires_at": None,
        "status": "active",
        "content_hash": content_hash(description or normalised.title),
    }


def fetch_reed(search: Search) -> list[dict[str, Any]]:
    auth = (settings.reed_api_key, "")
    params: dict[str, Any] = {"keywords": search.role, "resultsToTake": RESULTS_PER_SEARCH}
    if search.location:
        params.update({"locationName": search.location, "distanceFromLocation": 20})
    response = requests.get(REED_SEARCH_URL, params=params, auth=auth, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=7)
    records = []
    for item in response.json().get("results", []):
        posted = _reed_date(item.get("date"))
        if posted is not None and posted < cutoff:
            continue
        job_id = item.get("jobId")
        description = item.get("jobDescription") or ""
        try:
            detail = requests.get(
                REED_DETAIL_URL.format(job_id=job_id), auth=auth, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SECONDS
            )
            if detail.ok:
                body = detail.json()
                description = extract_text(body.get("jobDescription") or "") or description
                item = {**item, **{key: value for key, value in body.items() if value is not None}}
        except requests.RequestException:
            LOGGER.warning("reed_detail_failed job_id=%s", job_id)
        records.append(
            {
                "id": f"reed-{job_id}",
                "title": item.get("jobTitle") or "",
                "company": item.get("employerName"),
                "location": item.get("locationName"),
                "url": item.get("jobUrl") or f"https://www.reed.co.uk/jobs/{job_id}",
                "description": description.strip(),
                "salary_min": _decimal(item.get("yearlyMinimumSalary") or item.get("minimumSalary")),
                "salary_max": _decimal(item.get("yearlyMaximumSalary") or item.get("maximumSalary")),
                "employment_type": item.get("contractType"),
                "posted_at": posted,
            }
        )
    return [record for record in records if record["title"]]


def fetch_adzuna(search: Search) -> list[dict[str, Any]]:
    params: dict[str, Any] = {
        "app_id": settings.adzuna_app_id,
        "app_key": settings.adzuna_app_key,
        "what": search.role,
        "results_per_page": RESULTS_PER_SEARCH,
        "max_days_old": 7,
        "sort_by": "date",
        "content-type": "application/json",
    }
    if search.location:
        params.update({"where": search.location, "distance": 32})
    response = requests.get(ADZUNA_SEARCH_URL, params=params, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    records = []
    for item in response.json().get("results", []):
        job_id = item.get("id")
        # Adzuna estimates salaries it wasn't given; only keep real ones.
        real_salary = str(item.get("salary_is_predicted", "0")) != "1"
        records.append(
            {
                "id": f"adzuna-{job_id}",
                "title": _strip_tags(item.get("title") or ""),
                "company": (item.get("company") or {}).get("display_name"),
                "location": (item.get("location") or {}).get("display_name"),
                "url": f"https://www.adzuna.co.uk/jobs/details/{job_id}",
                "description": _strip_tags(item.get("description") or ""),
                "salary_min": _decimal(item.get("salary_min")) if real_salary else None,
                "salary_max": _decimal(item.get("salary_max")) if real_salary else None,
                "employment_type": item.get("contract_time"),
                "posted_at": _iso_date(item.get("created")),
            }
        )
    return [record for record in records if record["title"]]


def _reed_date(value: str | None) -> datetime | None:
    try:
        return datetime.strptime(value or "", "%d/%m/%Y").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _iso_date(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat((value or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value)) if value not in (None, "", 0) else None
    except InvalidOperation:
        return None


def _strip_tags(value: str) -> str:
    return re.sub(r"<[^>]+>", "", value).strip()
