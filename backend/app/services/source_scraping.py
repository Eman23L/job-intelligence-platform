import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.models import Job, JobSource, RawJobSnapshot, ScrapeRun, User
from app.db.session import SessionLocal
from app.schemas.database import (
    JobServeSearchScrapeRequest,
    JobServeSearchScrapeResult,
    ScrapeNowRequest,
    ScrapeNowResult,
    ScrapeRunStatus,
    ScrapeStartResult,
    SourceScrapeRunStart,
    SourceScrapeRunStatus,
    SourceTestResult,
)
from app.scrapers.parsers.html import extract_text
from app.scrapers.parsers.json_ld import extract_job_postings
from app.scrapers.parsers.job_detail import extract_page_title
from app.scrapers.policies.robots import check_robots_allowed
from app.scrapers.registry import adapter_registry
from app.scrapers.job_boards import JobRecord
from app.scrapers.jobserve import JobServeAdapter, JobServeSourceAdapter, is_jobserve_search_page
from app.scrapers.jobserve import extract_jobserve_job_ids
from app.scrapers.utils.hashing import content_hash
from app.services.analysis import analyse_job
from app.services.job_validation import validate_normalised_job
from app.services.job_availability import check_jobs_availability
from app.services.link_discovery import discover_links
from app.services.normalisation import normalise_job_fields
from app.services.scoring import score_job


LOGGER = logging.getLogger(__name__)
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
DEFAULT_DELAY_SECONDS = 8.0
JOBSERVE_SEARCH_SOURCE_NAME = "JobServe Search"


@dataclass(frozen=True)
class FetchResult:
    url: str
    status_code: int
    text: str
    content_type: str | None = None


@dataclass(frozen=True)
class JobServeSearchResultPage:
    url: str
    html: str
    job_ids: list[str]
    status_code: int
    cookies: dict[str, str]


def create_source_from_url(db: Session, payload) -> JobSource:
    base_url = str(payload.base_url).strip()
    robots_url = urljoin(_site_root(base_url), "/robots.txt")
    source = JobSource(
        name=payload.name,
        base_url=base_url,
        source_type=payload.source_type,
        robots_url=robots_url,
        scraping_allowed=payload.scraping_allowed,
        permission_notes=payload.permission_notes,
        rate_limit_per_minute=payload.rate_limit_per_minute,
        allowed_path_patterns=payload.allowed_path_patterns,
        job_link_patterns=payload.job_link_patterns,
        enabled=payload.scraping_allowed,
        last_reviewed_at=datetime.now(tz=timezone.utc),
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    return source


def test_source_url(db: Session, source: JobSource, target_url: str | None = None) -> SourceTestResult:
    url = target_url or source.base_url
    warnings, errors = _validate_source_for_fetch(source, url)
    if errors:
        return SourceTestResult(
            can_fetch=False,
            status_code=None,
            page_title=None,
            links_found_count=0,
            likely_job_links_count=0,
            sample_job_links=[],
            discovered_job_ids=[],
            warnings=warnings,
            errors=errors,
        )

    delay = effective_delay_seconds(source.rate_limit_per_minute, DEFAULT_DELAY_SECONDS)
    try:
        fetched = fetch_url(url, delay_seconds=delay)
    except requests.RequestException as exc:
        LOGGER.info("source test fetch failed url=%s error=%s", url, exc)
        return SourceTestResult(
            can_fetch=False,
            status_code=None,
            page_title=None,
            links_found_count=0,
            likely_job_links_count=0,
            sample_job_links=[],
            discovered_job_ids=[],
            warnings=warnings,
            errors=[str(exc)],
        )
    adapter = _adapter_for_source_url(source, fetched.url)
    links = discover_links(
        fetched.text,
        fetched.url,
        allow_patterns=source.allowed_path_patterns,
        job_link_patterns=source.job_link_patterns,
    )
    discovered = adapter.discover_jobs(fetched.text, fetched.url)
    warnings.extend(links.warnings)
    warnings.extend(discovered.warnings)
    candidate_count = len(links.likely_job_links) + len(discovered.discovered_job_ids)
    return SourceTestResult(
        can_fetch=200 <= fetched.status_code < 400,
        status_code=fetched.status_code,
        page_title=extract_page_title(fetched.text),
        links_found_count=len(links.links),
        likely_job_links_count=candidate_count,
        sample_job_links=discovered.job_urls[:10],
        discovered_job_ids=discovered.discovered_job_ids[:20],
        warnings=warnings,
        errors=[] if 200 <= fetched.status_code < 400 else [f"HTTP {fetched.status_code}"],
    )


def scrape_source_now(db: Session, source: JobSource, payload: ScrapeNowRequest) -> ScrapeNowResult:
    return execute_scrape_source_now(db, source, payload)


def search_scrape_jobserve(
    db: Session,
    payload: JobServeSearchScrapeRequest,
    *,
    scrape_run_id: int | None = None,
) -> JobServeSearchScrapeResult:
    source = _get_or_create_jobserve_search_source(db)
    try:
        search_page = _fetch_jobserve_search_results(payload)
    except Exception as exc:  # noqa: BLE001
        return JobServeSearchScrapeResult(
            source_id=source.id,
            search_url=build_jobserve_search_url(payload),
            jobs_found=0,
            jobs_created=0,
            jobs_updated=0,
            jobs_skipped=0,
            parsed_jobs=[],
            errors=[str(exc)],
            warnings=[],
        )
    search_url = search_page.url
    warnings: list[str] = []
    errors: list[str] = []
    created = updated = skipped = 0
    parsed_jobs: list[dict[str, str | None]] = []

    if search_page.status_code >= 400:
        return JobServeSearchScrapeResult(
            source_id=source.id,
            search_url=search_url,
            jobs_found=0,
            jobs_created=0,
            jobs_updated=0,
            jobs_skipped=0,
            parsed_jobs=[],
            errors=[f"JobServe search returned HTTP {search_page.status_code}"],
            warnings=warnings,
        )

    page_size = _hidden_int(search_page.html, "pgSize", default=25)
    job_ids = search_page.job_ids[: max(1, payload.max_pages) * page_size]
    _update_scrape_run_progress(db, scrape_run_id, found=len(job_ids), created=created, updated=updated, skipped=skipped)
    if not job_ids:
        warnings.append("JobServe search completed but no hidden job IDs were found in the result HTML.")

    detail_started = time.perf_counter()
    records, detail_errors = _fetch_jobserve_detail_records(
        job_ids,
        referer=search_url,
        delay_seconds=1,
        max_workers=1,
    )
    LOGGER.info(
        "JobServe search detail fetch completed search_url=%s detail_requests=%s records=%s failures=%s duration_ms=%s",
        search_url,
        len(job_ids),
        len(records),
        len(detail_errors),
        int((time.perf_counter() - detail_started) * 1000),
    )
    errors.extend(detail_errors)
    parsed_jobs = [_job_record_summary(record) for record in records]

    for record in records:
        try:
            normalised = _normalise_jobserve_record(source, record)
            validation = validate_normalised_job(normalised, source_name=source.name)
            if not validation.is_valid:
                skipped += 1
                warnings.append(_rejection_message("JobServe search", normalised, validation.reasons))
                _update_scrape_run_progress(db, scrape_run_id, found=len(job_ids), created=created, updated=updated, skipped=skipped)
                continue
            db.add(
                RawJobSnapshot(
                    source_id=source.id,
                    source_job_id=normalised["source_job_id"],
                    url=normalised["canonical_url"],
                    raw_html=None,
                    raw_text=record.description,
                    raw_json=(record.json_ld or [None])[0],
                    content_hash=normalised["content_hash"],
                )
            )
            was_created = _upsert_job(db, source, normalised)
            if was_created:
                created += 1
            else:
                updated += 1
            _update_scrape_run_progress(db, scrape_run_id, found=len(job_ids), created=created, updated=updated, skipped=skipped)
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            skipped += 1
            errors.append(f"JobServe search {record.source_job_id}: {exc}")
            _update_scrape_run_progress(db, scrape_run_id, found=len(job_ids), created=created, updated=updated, skipped=skipped)
            continue
    db.commit()
    return JobServeSearchScrapeResult(
        source_id=source.id,
        search_url=search_url,
        jobs_found=len(job_ids),
        jobs_created=created,
        jobs_updated=updated,
        jobs_skipped=skipped,
        parsed_jobs=parsed_jobs,
        errors=errors,
        warnings=warnings,
    )


def start_jobserve_search_scrape(db: Session, payload: JobServeSearchScrapeRequest) -> SourceScrapeRunStart:
    source = _get_or_create_jobserve_search_source(db)
    run = ScrapeRun(
        source_id=source.id,
        status="running",
        started_at=datetime.now(tz=timezone.utc),
        errors=[],
        parsed_jobs=[],
        jobs_found=0,
        jobs_created=0,
        jobs_updated=0,
        jobs_skipped=0,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    LOGGER.info(
        "JobServe search scrape queued run_id=%s keywords=%s location=%s max_pages=%s",
        run.id,
        payload.keywords,
        payload.location,
        payload.max_pages,
    )
    return SourceScrapeRunStart(run_id=run.id, status="running")


def run_jobserve_search_scrape_background(scrape_run_id: int, payload_data: dict[str, Any]) -> None:
    started = time.perf_counter()
    payload = JobServeSearchScrapeRequest(**payload_data)
    with SessionLocal() as db:
        run = db.get(ScrapeRun, scrape_run_id)
        if run is None:
            return
        try:
            result = search_scrape_jobserve(db, payload, scrape_run_id=scrape_run_id)
            run = db.get(ScrapeRun, scrape_run_id)
            if run is None:
                return
            run.status = "failed" if result.errors else "completed"
            run.finished_at = datetime.now(tz=timezone.utc)
            run.jobs_found = result.jobs_found
            run.jobs_created = result.jobs_created
            run.jobs_updated = result.jobs_updated
            run.jobs_skipped = result.jobs_skipped
            run.parsed_jobs = [item.model_dump() if hasattr(item, "model_dump") else item for item in result.parsed_jobs]
            run.errors = result.errors
            run.error_message = "; ".join(result.errors[:5]) if result.errors else None
            db.commit()
            LOGGER.info(
                "JobServe search scrape finished run_id=%s status=%s duration_ms=%s pages_requested=%s details_found=%s failures=%s",
                scrape_run_id,
                run.status,
                int((time.perf_counter() - started) * 1000),
                payload.max_pages,
                result.jobs_found,
                len(result.errors),
            )
            if payload.check_availability_after and run.status == "completed":
                try:
                    job_ids = list(db.scalars(select(Job.id).where(Job.source_id == result.source_id)).all())
                    check_jobs_availability(db, job_ids)
                    LOGGER.info("JobServe post-scrape availability check completed run_id=%s", scrape_run_id)
                except Exception:
                    LOGGER.exception("JobServe post-scrape availability check failed run_id=%s", scrape_run_id)
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            run = db.get(ScrapeRun, scrape_run_id)
            if run is not None:
                run.status = "failed"
                run.finished_at = datetime.now(tz=timezone.utc)
                run.error_message = str(exc)
                run.errors = [str(exc)]
                db.commit()
            LOGGER.exception(
                "JobServe search scrape failed run_id=%s duration_ms=%s",
                scrape_run_id,
                int((time.perf_counter() - started) * 1000),
            )


def get_source_scrape_run_status(db: Session, run_id: int) -> SourceScrapeRunStatus | None:
    run = db.get(ScrapeRun, run_id)
    if run is None:
        return None
    return SourceScrapeRunStatus(
        run_id=run.id,
        status=run.status,
        found=run.jobs_found or 0,
        created=run.jobs_created or 0,
        updated=run.jobs_updated or 0,
        skipped=run.jobs_skipped or 0,
        error=run.error_message,
    )


def _update_scrape_run_progress(
    db: Session,
    scrape_run_id: int | None,
    *,
    found: int,
    created: int,
    updated: int,
    skipped: int,
) -> None:
    if scrape_run_id is None:
        return
    run = db.get(ScrapeRun, scrape_run_id)
    if run is None:
        return
    run.jobs_found = found
    run.jobs_created = created
    run.jobs_updated = updated
    run.jobs_skipped = skipped
    db.commit()


def build_jobserve_search_url(payload: JobServeSearchScrapeRequest) -> str:
    return "https://www.jobserve.com/gb/en/Job-Search/"


def _fetch_jobserve_search_results(payload: JobServeSearchScrapeRequest) -> JobServeSearchResultPage:
    base_url = build_jobserve_search_url(payload)
    session = requests.Session()
    headers = {"User-Agent": BROWSER_USER_AGENT}
    initial = session.get(base_url, headers=headers, timeout=20)
    initial.raise_for_status()
    form_data, action_url = _jobserve_search_form_payload(initial.text, base_url, payload)
    response = session.post(
        action_url,
        data=form_data,
        headers={**headers, "Referer": base_url},
        timeout=30,
        allow_redirects=True,
    )
    return JobServeSearchResultPage(
        url=response.url,
        html=response.text,
        job_ids=extract_jobserve_job_ids(response.text),
        status_code=response.status_code,
        cookies=session.cookies.get_dict(),
    )


def _jobserve_search_form_payload(html: str, base_url: str, payload: JobServeSearchScrapeRequest) -> tuple[dict[str, Any], str]:
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form", id="frm1") or soup.find("form")
    if form is None:
        raise ValueError("JobServe search form not found")
    data: dict[str, Any] = {}
    for field in form.find_all("input"):
        name = field.get("name")
        if not name:
            continue
        input_type = str(field.get("type") or "text").lower()
        if input_type == "submit":
            continue
        if input_type in {"checkbox", "radio"}:
            if field.has_attr("checked"):
                data[name] = field.get("value") or "on"
            continue
        data[name] = field.get("value") or ""
    for field in form.find_all("select"):
        name = field.get("name")
        if not name:
            continue
        selected = [option.get("value") or "" for option in field.find_all("option") if option.has_attr("selected")]
        if field.has_attr("multiple"):
            data[name] = selected
        else:
            first = field.find("option")
            data[name] = selected[0] if selected else (first.get("value") if first else "")

    data["ctl00$main$srch$ctl_qs$txtKey"] = payload.keywords.strip()
    data["ctl00$main$srch$ctl_qs$txtLoc"] = (payload.location or "").strip()
    data["selAge"] = str(payload.posted_within_days or 7)
    data["ctl00$main$srch$ctl_qs$btnSearch"] = "Search"
    remote_name = "ctl00$main$srch$ctl_qs$RemoteWorking$chkRemoteWorking"
    if payload.remote_only:
        data[remote_name] = "on"
    else:
        data.pop(remote_name, None)
    action_url = urljoin(base_url, form.get("action") or "./JobServeHome.aspx")
    return data, action_url


def _hidden_int(html: str, element_id: str, *, default: int) -> int:
    soup = BeautifulSoup(html, "html.parser")
    field = soup.find("input", id=element_id)
    try:
        return int(str(field.get("value"))) if field else default
    except (TypeError, ValueError):
        return default


def start_scrape_source_now(db: Session, source: JobSource, payload: ScrapeNowRequest) -> ScrapeStartResult:
    run = ScrapeRun(source_id=source.id, status="pending", errors=[], parsed_jobs=[])
    db.add(run)
    db.commit()
    db.refresh(run)
    LOGGER.info("scrape queued run_id=%s source_id=%s at=%s", run.id, source.id, datetime.now(tz=timezone.utc).isoformat())
    return ScrapeStartResult(status="started", scrape_run_id=run.id)


def run_scrape_background(scrape_run_id: int, source_id: int, payload_data: dict[str, Any]) -> None:
    with SessionLocal() as db:
        source = db.get(JobSource, source_id)
        if source is None:
            run = db.get(ScrapeRun, scrape_run_id)
            if run:
                run.status = "failed"
                run.finished_at = datetime.now(tz=timezone.utc)
                run.error_message = "Source not found"
                run.errors = ["Source not found"]
                db.commit()
            return
        payload = ScrapeNowRequest(**payload_data)
        execute_scrape_source_now(db, source, payload, scrape_run_id=scrape_run_id)


def get_scrape_run_status(db: Session, scrape_run_id: int) -> ScrapeRunStatus | None:
    run = db.get(ScrapeRun, scrape_run_id)
    if run is None:
        return None
    return ScrapeRunStatus(
        status=run.status,
        jobs_found=run.jobs_found,
        jobs_created=run.jobs_created,
        jobs_updated=run.jobs_updated,
        jobs_skipped=run.jobs_skipped,
        errors=run.errors or ([run.error_message] if run.error_message else []),
        parsed_jobs=run.parsed_jobs or [],
    )


def execute_scrape_source_now(
    db: Session,
    source: JobSource,
    payload: ScrapeNowRequest,
    *,
    scrape_run_id: int | None = None,
) -> ScrapeNowResult:
    scrape_started = time.perf_counter()
    LOGGER.info(
        "scrape start run_id=%s source_id=%s dry_run=%s at=%s",
        scrape_run_id,
        source.id,
        payload.dry_run,
        datetime.now(tz=timezone.utc).isoformat(),
    )
    start_url = payload.start_url or source.base_url
    max_pages = max(1, min(payload.max_pages, 50))
    max_jobs = max(1, min(payload.max_jobs, 100))
    delay = effective_delay_seconds(source.rate_limit_per_minute, payload.delay_seconds or DEFAULT_DELAY_SECONDS)
    warnings, errors = _validate_source_for_fetch(source, start_url)
    run = db.get(ScrapeRun, scrape_run_id) if scrape_run_id is not None else None
    if run is not None:
        run.status = "running"
        run.started_at = datetime.now(tz=timezone.utc)
        run.errors = []
        run.parsed_jobs = []
        db.commit()
    if errors:
        if run is not None:
            run.status = "failed"
            run.finished_at = datetime.now(tz=timezone.utc)
            run.error_message = "; ".join(errors[:5])
            run.errors = errors
            db.commit()
        return ScrapeNowResult(
            scrape_run_id=run.id if run else None,
            jobs_found=0,
            jobs_created=0,
            jobs_updated=0,
            jobs_skipped=0,
            discovered_job_ids=[],
            parsed_jobs=[],
            errors=errors,
            warnings=warnings,
        )

    if run is None and not payload.dry_run:
        run = ScrapeRun(source_id=source.id, status="running")
        db.add(run)
        db.flush()

    found_urls: list[str] = []
    discovered_job_ids: list[str] = []
    parsed_jobs: list[dict[str, str | None]] = []
    jobs_found = 0
    created = updated = skipped = 0
    adapter = adapter_registry.get(source.source_type)(source)
    try:
        start_page = fetch_url(start_url, delay_seconds=0)
        LOGGER.info("scrape discovery fetched url=%s status=%s", start_url, start_page.status_code)
        adapter = _adapter_for_source_url(source, start_page.url)
        discovered = adapter.discover_jobs(start_page.text, start_page.url)
        warnings.extend(discovered.warnings)
        found_urls = discovered.job_urls[:max_jobs]
        discovered_job_ids = discovered.discovered_job_ids[:max_jobs]
        jobs_found = len(found_urls) + len(discovered_job_ids)
        if run:
            run.jobs_found = jobs_found
            db.commit()
        if payload.dry_run:
            if discovered_job_ids:
                detail_limit = min(3, len(discovered_job_ids))
                detail_started = time.perf_counter()
                records, detail_errors = _fetch_jobserve_detail_records(
                    discovered_job_ids[:detail_limit],
                    referer=start_page.url,
                    delay_seconds=delay,
                    max_workers=detail_limit,
                )
                LOGGER.info(
                    "JobServe detail fetch duration run_id=%s jobs=%s seconds=%.3f",
                    run.id if run else scrape_run_id,
                    len(records),
                    time.perf_counter() - detail_started,
                )
                errors.extend(detail_errors)
                parsed_jobs = [_job_record_summary(record) for record in records]
            if run:
                run.status = "completed" if not errors else "failed"
                run.jobs_found = jobs_found
                run.jobs_created = 0
                run.jobs_updated = 0
                run.jobs_skipped = 0
                run.finished_at = datetime.now(tz=timezone.utc)
                run.error_message = "; ".join(errors[:5]) if errors else None
                run.errors = errors
                run.parsed_jobs = parsed_jobs
                db.commit()
            return ScrapeNowResult(
                scrape_run_id=run.id if run else None,
                jobs_found=jobs_found,
                jobs_created=0,
                jobs_updated=0,
                jobs_skipped=0,
                discovered_job_ids=discovered_job_ids,
                parsed_jobs=parsed_jobs,
                errors=errors,
                warnings=warnings,
            )

        if discovered_job_ids:
            detail_started = time.perf_counter()
            records, detail_errors = _fetch_jobserve_detail_records(
                discovered_job_ids,
                referer=start_page.url,
                delay_seconds=delay,
                max_workers=min(8, len(discovered_job_ids)),
            )
            LOGGER.info(
                "JobServe detail fetch duration run_id=%s jobs=%s seconds=%.3f",
                run.id if run else scrape_run_id,
                len(records),
                time.perf_counter() - detail_started,
            )
            errors.extend(detail_errors)
            parsed_jobs = [_job_record_summary(record) for record in records]
            persistence_started = time.perf_counter()
            for record in records:
                try:
                    normalised = _normalise_jobserve_record(source, record)
                    validation = validate_normalised_job(normalised, source_name=source.name)
                    if not validation.is_valid:
                        skipped += 1
                        message = _rejection_message("JobServe", normalised, validation.reasons)
                        warnings.append(message)
                        LOGGER.info(message)
                        if run:
                            run.jobs_skipped = skipped
                            run.errors = errors
                            db.commit()
                        continue
                    snapshot = RawJobSnapshot(
                        source_id=source.id,
                        source_job_id=normalised["source_job_id"],
                        url=normalised["canonical_url"],
                        raw_html=None,
                        raw_text=record.description,
                        raw_json=(record.json_ld or [None])[0],
                        content_hash=normalised["content_hash"],
                    )
                    db.add(snapshot)
                    db.flush()
                    was_created = _upsert_job(db, source, normalised)
                    if was_created:
                        created += 1
                    else:
                        updated += 1
                    if run:
                        run.jobs_created = created
                        run.jobs_updated = updated
                        run.jobs_skipped = skipped
                        run.parsed_jobs = parsed_jobs
                        db.commit()
                    LOGGER.info(
                        "JobServe DB %s source_job_id=%s title=%s",
                        "insert" if was_created else "update",
                        normalised["source_job_id"],
                        normalised["title"],
                    )
                    job = db.scalar(select(Job).where(Job.source_id == source.id, Job.source_job_id == normalised["source_job_id"]))
                    if job is not None:
                        analyse_job(db, job)
                        _score_if_possible(db, job, warnings)
                except Exception as exc:  # noqa: BLE001 - one failed job should not stop the run.
                    db.rollback()
                    skipped += 1
                    errors.append(f"JobServe {record.source_job_id}: {exc}")
                    if run:
                        run.jobs_created = created
                        run.jobs_updated = updated
                        run.jobs_skipped = skipped
                        run.errors = errors
                        db.commit()
                    LOGGER.exception("JobServe DB persistence failed source_job_id=%s", record.source_job_id)
            LOGGER.info(
                "JobServe DB persistence duration run_id=%s seconds=%.3f",
                run.id if run else scrape_run_id,
                time.perf_counter() - persistence_started,
            )
            LOGGER.info("JobServe DB insert/update counts created=%s updated=%s skipped=%s", created, updated, skipped)

        for url in found_urls[:max_pages]:
            robot = check_robots_allowed(source.robots_url, url, BROWSER_USER_AGENT, fail_closed=True)
            if not robot.allowed:
                skipped += 1
                warnings.append(f"Skipped {url}: {robot.reason}")
                continue
            try:
                fetched = fetch_url(url, delay_seconds=delay)
                LOGGER.info("job page fetched url=%s status=%s", url, fetched.status_code)
                if fetched.status_code >= 400:
                    skipped += 1
                    errors.append(f"{url}: HTTP {fetched.status_code}")
                    continue
                parsed = adapter.parse_job_detail(fetched.text, fetched.url)
                normalised = adapter.normalise(parsed, fetched.url)
                validation = validate_normalised_job(normalised, source_name=source.name)
                if not validation.is_valid:
                    skipped += 1
                    message = _rejection_message("generic", normalised, validation.reasons)
                    warnings.append(message)
                    LOGGER.info(message)
                    if run:
                        run.jobs_skipped = skipped
                        run.errors = errors
                        db.commit()
                    continue
                raw_text = extract_text(fetched.text)
                raw_hash = content_hash(fetched.text)
                snapshot = RawJobSnapshot(
                    source_id=source.id,
                    source_job_id=None,
                    url=fetched.url,
                    raw_html=fetched.text,
                    raw_text=raw_text,
                    raw_json=(extract_job_postings(fetched.text) or [None])[0],
                    content_hash=raw_hash,
                )
                db.add(snapshot)
                db.flush()
                snapshot.source_job_id = normalised["source_job_id"]
                was_created = _upsert_job(db, source, normalised)
                if was_created:
                    created += 1
                else:
                    updated += 1
                if run:
                    run.jobs_created = created
                    run.jobs_updated = updated
                    run.jobs_skipped = skipped
                    db.commit()
                job = db.scalar(select(Job).where(Job.source_id == source.id, Job.source_job_id == normalised["source_job_id"]))
                if job is not None:
                    analyse_job(db, job)
                    _score_if_possible(db, job, warnings)
            except Exception as exc:  # noqa: BLE001 - each URL should fail independently.
                db.rollback()
                skipped += 1
                errors.append(f"{url}: {exc}")
                if run:
                    run.jobs_created = created
                    run.jobs_updated = updated
                    run.jobs_skipped = skipped
                    run.errors = errors
                    db.commit()
                LOGGER.info("job page failed url=%s error=%s", url, exc)

        if run:
            run.status = "completed" if not errors else "failed"
            run.jobs_found = jobs_found
            run.jobs_created = created
            run.jobs_updated = updated
            run.jobs_skipped = skipped
            run.finished_at = datetime.now(tz=timezone.utc)
            run.error_message = "; ".join(errors[:5]) if errors else None
            run.errors = errors
            run.parsed_jobs = parsed_jobs
        db.commit()
        LOGGER.info(
            "scrape completion run_id=%s status=%s jobs_found=%s created=%s updated=%s skipped=%s seconds=%.3f at=%s",
            run.id if run else scrape_run_id,
            run.status if run else ("failed" if errors else "completed"),
            jobs_found,
            created,
            updated,
            skipped,
            time.perf_counter() - scrape_started,
            datetime.now(tz=timezone.utc).isoformat(),
        )
        return ScrapeNowResult(
            scrape_run_id=run.id if run else None,
            jobs_found=jobs_found,
            jobs_created=created,
            jobs_updated=updated,
            jobs_skipped=skipped,
            discovered_job_ids=discovered_job_ids,
            parsed_jobs=parsed_jobs,
            errors=errors,
            warnings=warnings,
        )
    except Exception as exc:
        db.rollback()
        if run:
            run.status = "failed"
            run.error_message = str(exc)
            run.errors = [str(exc)]
            run.parsed_jobs = parsed_jobs
            run.finished_at = datetime.now(tz=timezone.utc)
            db.add(run)
            db.commit()
        LOGGER.exception(
            "scrape failed run_id=%s source_id=%s seconds=%.3f",
            run.id if run else scrape_run_id,
            source.id,
            time.perf_counter() - scrape_started,
        )
        return ScrapeNowResult(
            scrape_run_id=run.id if run else None,
            jobs_found=len(found_urls),
            jobs_created=created,
            jobs_updated=updated,
            jobs_skipped=skipped,
            discovered_job_ids=discovered_job_ids,
            parsed_jobs=parsed_jobs,
            errors=[str(exc)],
            warnings=warnings,
        )


def fetch_url(url: str, *, delay_seconds: float = 0) -> FetchResult:
    if delay_seconds > 0:
        time.sleep(delay_seconds)
    response = requests.get(url, headers={"User-Agent": BROWSER_USER_AGENT}, timeout=20)
    return FetchResult(
        url=response.url,
        status_code=response.status_code,
        text=response.text,
        content_type=response.headers.get("content-type"),
    )


def effective_delay_seconds(rate_limit_per_minute: int, requested_delay_seconds: float | None = None) -> float:
    configured = requested_delay_seconds if requested_delay_seconds is not None else DEFAULT_DELAY_SECONDS
    rate_delay = 60.0 / rate_limit_per_minute if rate_limit_per_minute > 0 else DEFAULT_DELAY_SECONDS
    return max(float(configured), rate_delay, DEFAULT_DELAY_SECONDS)


def _validate_source_for_fetch(source: JobSource, url: str) -> tuple[list[str], list[str]]:
    warnings: list[str] = []
    errors: list[str] = []
    if "linkedin.com" in url.lower() or "linkedin.com" in source.base_url.lower():
        errors.append("LinkedIn scraping is excluded.")
    if not source.scraping_allowed:
        errors.append("scraping_allowed is false")
    if not source.enabled:
        errors.append("source is disabled")
    if not source.permission_notes or not source.permission_notes.strip():
        errors.append("permission_notes are missing")
    robot = check_robots_allowed(source.robots_url, url, BROWSER_USER_AGENT, fail_closed=True)
    if not robot.allowed:
        errors.append(robot.reason)
    else:
        warnings.append(robot.reason)
    return warnings, errors


def _fetch_jobserve_detail_records(
    job_ids: list[str],
    *,
    referer: str,
    delay_seconds: float,
    max_workers: int,
) -> tuple[list[JobRecord], list[str]]:
    adapter = JobServeAdapter(min_delay_seconds=delay_seconds)
    adapter.detail_referer = referer
    records: list[JobRecord] = []
    errors: list[str] = []
    workers = max(1, min(max_workers, len(job_ids) or 1))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_id = {executor.submit(adapter._fetch_and_parse_job, job_id): job_id for job_id in job_ids}
        for future in as_completed(future_to_id):
            job_id = future_to_id[future]
            try:
                record = future.result()
            except requests.Timeout as exc:
                message = f"JobServe {job_id}: detail request timed out"
                errors.append(message)
                LOGGER.warning("%s error=%s", message, exc)
                continue
            except Exception as exc:  # noqa: BLE001 - detail failures should be reported per job.
                message = f"JobServe {job_id}: {exc}"
                errors.append(message)
                LOGGER.warning("JobServe detail parsing/fetch failed job_id=%s error=%s", job_id, exc)
                continue
            records.append(record)
            LOGGER.info("JobServe parsing success job_id=%s title=%s", job_id, record.title)
    return records, errors


def _normalise_jobserve_record(source: JobSource, record: JobRecord) -> dict[str, Any]:
    description = record.description or ""
    if record.skills:
        description = f"{description}\n\nSkills: {', '.join(record.skills)}".strip()
    title = record.title or "Untitled JobServe job"
    apply_link = record.apply_link if record.apply_link and not record.apply_link.lower().startswith("javascript:") else None
    canonical_url = apply_link or record.url or f"https://www.jobserve.com/gb/en/job/{record.source_job_id or content_hash(title)}"
    normalised = normalise_job_fields(
        title=title,
        company_name=record.recruiter,
        location=record.location,
        employment_type=record.employment_type,
        salary_text=record.salary,
        posted_date=record.posted_date,
        canonical_url=canonical_url,
        description_text=description,
    )
    source_job_id = record.source_job_id or content_hash(canonical_url)
    return {
        "source_id": source.id,
        "source_job_id": source_job_id,
        "canonical_url": normalised.canonical_url,
        "original_title": normalised.title,
        "original_company": normalised.company_name,
        "original_location": normalised.location,
        "original_salary": record.salary,
        "original_external_id": source_job_id,
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
        "content_hash": content_hash(description),
    }


def _job_record_summary(record: JobRecord) -> dict[str, str | None]:
    return {
        "source_job_id": record.source_job_id,
        "title": record.title,
        "company_name": record.recruiter,
        "location": record.location,
        "canonical_url": record.url,
    }


def _rejection_message(source_kind: str, item: dict[str, Any], reasons: list[str]) -> str:
    return (
        f"Rejected {source_kind} candidate source_job_id={item.get('source_job_id')} "
        f"title={item.get('title')!r} url={item.get('canonical_url')} reasons={'; '.join(reasons)}"
    )


def _upsert_job(db: Session, source: JobSource, item: dict) -> bool:
    _ensure_fingerprint_fields(item)
    existing = db.scalar(
        select(Job).where(
            or_(
                (Job.source_id == source.id) & (Job.source_job_id == item["source_job_id"]),
                Job.canonical_url == item["canonical_url"],
            )
        )
    )
    if existing is None:
        db.add(Job(**item))
        db.flush()
        return True
    for field, value in item.items():
        if field.startswith("original_") and getattr(existing, field, None):
            continue
        setattr(existing, field, value)
    existing.last_seen_at = datetime.now(tz=timezone.utc)
    db.flush()
    return False


def _ensure_fingerprint_fields(item: dict) -> None:
    item.setdefault("original_title", item.get("title"))
    item.setdefault("original_company", item.get("company_name"))
    item.setdefault("original_location", item.get("location"))
    item.setdefault("original_salary", _salary_fingerprint(item))
    item.setdefault("original_external_id", item.get("source_job_id"))


def _salary_fingerprint(item: dict) -> str | None:
    currency = item.get("salary_currency")
    minimum = item.get("salary_min_raw") or item.get("salary_min")
    maximum = item.get("salary_max_raw") or item.get("salary_max")
    if minimum is None and maximum is None:
        return None
    if minimum is not None and maximum is not None and minimum != maximum:
        return " ".join(str(part) for part in [currency, f"{minimum}-{maximum}"] if part)
    value = minimum if minimum is not None else maximum
    return " ".join(str(part) for part in [currency, value] if part)


def _score_if_possible(db: Session, job: Job, warnings: list[str]) -> None:
    user = db.scalar(select(User).order_by(User.id))
    if user is None:
        warnings.append("Job analysed but not scored because no seeded user exists.")
        return
    score_job(db, job, user=user)


def _site_root(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _adapter_for_source_url(source: JobSource, url: str):
    if is_jobserve_search_page(url):
        return JobServeSourceAdapter(source)
    return adapter_registry.get(source.source_type)(source)


def _get_or_create_jobserve_search_source(db: Session) -> JobSource:
    source = db.scalar(select(JobSource).where(JobSource.name == JOBSERVE_SEARCH_SOURCE_NAME))
    if source is not None:
        return source
    source = JobSource(
        name=JOBSERVE_SEARCH_SOURCE_NAME,
        base_url="https://www.jobserve.com/gb/en/JobSearch.aspx",
        source_type="jobserve",
        robots_url="https://www.jobserve.com/robots.txt",
        terms_url="https://www.jobserve.com/gb/en/terms",
        scraping_allowed=True,
        permission_notes="Search-driven JobServe scraping requested by the local operator.",
        rate_limit_per_minute=8,
        allowed_path_patterns=["/gb/en/JobSearch.aspx", "/job/", "/apply/"],
        job_link_patterns=["/job/", "/apply/", "/g"],
        enabled=True,
        last_reviewed_at=datetime.now(tz=timezone.utc),
    )
    db.add(source)
    db.flush()
    return source
