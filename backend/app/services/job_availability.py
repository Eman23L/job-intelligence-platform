from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup
import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Job
from app.schemas.database import JobAvailabilityResult

AVAILABILITY_ACTIVE = "active"
AVAILABILITY_EXPIRED = "expired"
AVAILABILITY_UNAVAILABLE = "unavailable"
AVAILABILITY_REDIRECTED = "redirected"
AVAILABILITY_UNKNOWN = "unknown"
QUEUEABLE_AVAILABILITY_STATUSES = {AVAILABILITY_ACTIVE, AVAILABILITY_UNKNOWN}

CLOSED_PHRASES = (
    "no longer available",
    "job expired",
    "application closed",
    "vacancy closed",
    "position filled",
    "this job is no longer accepting applications",
)

APPLY_TEXT_PATTERN = re.compile(r"\b(apply|apply now|start application|submit application)\b", re.IGNORECASE)
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}


@dataclass(frozen=True)
class FetchResult:
    final_url: str | None
    status_code: int | None
    text: str
    redirected: bool
    error: str | None = None


Fetcher = Callable[[str], FetchResult]


def check_job_availability(db: Session, job: Job, fetcher: Fetcher | None = None) -> JobAvailabilityResult:
    fetch_result = (fetcher or _fetch_job_page)(job.canonical_url)
    checked_at = datetime.now(tz=timezone.utc)
    status, reason = _classify_availability(job, fetch_result)
    job.availability_status = status
    job.last_checked_at = checked_at
    job.availability_reason = reason
    db.commit()
    db.refresh(job)
    return JobAvailabilityResult(
        job_id=job.id,
        availability_status=job.availability_status,
        last_checked_at=job.last_checked_at or checked_at,
        availability_reason=job.availability_reason,
        final_url=fetch_result.final_url,
        status_code=fetch_result.status_code,
    )


def check_jobs_availability(db: Session, job_ids: list[int] | None = None, fetcher: Fetcher | None = None) -> list[JobAvailabilityResult]:
    query = select(Job).order_by(Job.id)
    if job_ids:
        query = query.where(Job.id.in_(job_ids))
    jobs = db.scalars(query).all()
    return [check_job_availability(db, job, fetcher=fetcher) for job in jobs]


def _fetch_job_page(url: str) -> FetchResult:
    if urlparse(url).hostname and urlparse(url).hostname.endswith(".invalid"):
        return FetchResult(
            final_url=url,
            status_code=None,
            text="",
            redirected=False,
            error="Reserved .invalid host cannot be fetched",
        )
    try:
        response = requests.get(url, headers=REQUEST_HEADERS, timeout=8, allow_redirects=True)
    except requests.RequestException as exc:
        return FetchResult(final_url=None, status_code=None, text="", redirected=False, error=str(exc))
    return FetchResult(
        final_url=response.url,
        status_code=response.status_code,
        text=response.text or "",
        redirected=_normalise_url(response.url) != _normalise_url(url),
    )


def _classify_availability(job: Job, result: FetchResult) -> tuple[str, str]:
    if result.error:
        return AVAILABILITY_UNKNOWN, f"Could not fetch job page: {result.error}"
    if result.status_code in {404, 410}:
        return AVAILABILITY_UNAVAILABLE, f"Job page returned HTTP {result.status_code}"
    if result.status_code == 403:
        return AVAILABILITY_UNAVAILABLE, "Job page returned HTTP 403"
    if result.status_code is None:
        return AVAILABILITY_UNKNOWN, "No HTTP status returned"
    if result.status_code >= 500:
        return AVAILABILITY_UNKNOWN, f"Job page returned HTTP {result.status_code}"
    if result.status_code >= 400:
        return AVAILABILITY_UNAVAILABLE, f"Job page returned HTTP {result.status_code}"

    text = _page_text(result.text)
    lower_text = text.lower()
    for phrase in CLOSED_PHRASES:
        if phrase in lower_text:
            return AVAILABILITY_EXPIRED, f"Page contains closed phrase: {phrase}"

    if result.redirected:
        return AVAILABILITY_REDIRECTED, f"Job URL redirected to {result.final_url}"

    title_match = _contains_tokenised_text(text, job.title)
    company_match = _contains_tokenised_text(text, job.company_name) if job.company_name else None
    apply_exists = _has_apply_action(result.text)

    if apply_exists and (title_match or company_match or company_match is None):
        return AVAILABILITY_ACTIVE, "Job page still shows role details and an apply action"
    if apply_exists:
        return AVAILABILITY_ACTIVE, "Job page has an apply action"
    if title_match or company_match:
        return AVAILABILITY_UNKNOWN, "Job details still appear but no apply action was detected"
    return AVAILABILITY_UNKNOWN, "Could not verify title, company, or apply action on the job page"


def _page_text(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for element in soup(["script", "style", "noscript"]):
        element.decompose()
    return soup.get_text(" ", strip=True)


def _has_apply_action(html: str) -> bool:
    if not html:
        return False
    soup = BeautifulSoup(html, "html.parser")
    for element in soup.find_all(["a", "button", "input"]):
        label = " ".join(
            str(value)
            for value in [
                element.get_text(" ", strip=True),
                element.get("aria-label"),
                element.get("title"),
                element.get("value"),
                element.get("href"),
            ]
            if value
        )
        if APPLY_TEXT_PATTERN.search(label):
            return True
    return False


def _contains_tokenised_text(page_text: str, expected: str | None) -> bool:
    if not page_text or not expected:
        return False
    page = _normalise_words(page_text)
    target = _normalise_words(expected)
    return bool(target and target in page)


def _normalise_words(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value.lower())).strip()


def _normalise_url(value: str | None) -> str:
    if not value:
        return ""
    parsed = urlparse(value)
    path = parsed.path.rstrip("/")
    return parsed._replace(path=path, fragment="").geturl()
