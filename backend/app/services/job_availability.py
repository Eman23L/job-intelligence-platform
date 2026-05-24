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
from app.scrapers.parsers.job_detail import parse_job_detail
from app.scrapers.utils.hashing import content_hash
from app.schemas.database import JobAvailabilityResult

AVAILABILITY_ACTIVE = "active"
AVAILABILITY_EXPIRED = "expired"
AVAILABILITY_UNAVAILABLE = "unavailable"
AVAILABILITY_REDIRECTED = "redirected"
AVAILABILITY_REPLACED = "replaced"
AVAILABILITY_UNKNOWN = "unknown"
QUEUEABLE_AVAILABILITY_STATUSES = {AVAILABILITY_ACTIVE}

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


@dataclass(frozen=True)
class CurrentJobPage:
    title: str | None
    company: str | None
    location: str | None
    salary: str | None
    body_text: str
    content_hash: str | None
    apply_exists: bool
    is_jobserve_results_page: bool


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
        return AVAILABILITY_UNAVAILABLE, f"Could not access job page: {result.error}"
    if result.status_code in {404, 410}:
        return AVAILABILITY_UNAVAILABLE, f"Job page returned HTTP {result.status_code}"
    if result.status_code == 403:
        return AVAILABILITY_UNAVAILABLE, "Job page returned HTTP 403"
    if result.status_code is None:
        return AVAILABILITY_UNAVAILABLE, "No HTTP status returned"
    if result.status_code >= 500:
        return AVAILABILITY_UNKNOWN, f"Job page returned HTTP {result.status_code}"
    if result.status_code >= 400:
        return AVAILABILITY_UNAVAILABLE, f"Job page returned HTTP {result.status_code}"

    raw_text = _page_text(result.text)
    lower_text = raw_text.lower()
    for phrase in CLOSED_PHRASES:
        if phrase in lower_text:
            return AVAILABILITY_EXPIRED, f"Page contains closed phrase: {phrase}"

    current = _extract_current_job_page(result.text, result.final_url or job.canonical_url)
    original_title = job.original_title or job.title
    original_company = job.original_company or job.company_name
    original_location = job.original_location or job.location
    original_salary = job.original_salary
    original_hash = job.content_hash

    if current.is_jobserve_results_page:
        if _strong_text_match(original_title, current.title) and _optional_strong_match(original_company, current.company):
            if current.apply_exists:
                return AVAILABILITY_ACTIVE, "JobServe selected job matches the stored title and company and has an apply action"
            return AVAILABILITY_UNKNOWN, "JobServe selected job matches, but no apply action was detected"
        if current.title:
            return (
                AVAILABILITY_REPLACED,
                _changed_reason("JobServe page loaded but selected job title changed", original_title, current.title),
            )
        return AVAILABILITY_REPLACED, "JobServe search/results page loaded instead of the stored job detail"

    title_match = _strong_text_match(original_title, current.title) or _contains_tokenised_text(current.body_text, original_title)
    company_match = _optional_strong_match(original_company, current.company) or (
        bool(original_company) and _contains_tokenised_text(current.body_text, original_company)
    )
    company_required = bool(original_company)
    strong_identity_match = title_match and (company_match if company_required else True)

    title_mismatch = bool(current.title and original_title and not _strong_text_match(original_title, current.title))
    company_mismatch = bool(current.company and original_company and not _strong_text_match(original_company, current.company))
    content_mismatch = _content_mismatch(original_hash, current.content_hash, job.description_text, current.body_text)

    if strong_identity_match and current.apply_exists:
        return AVAILABILITY_ACTIVE, "Stored title/company match the current page and an apply action exists"
    if title_mismatch:
        return AVAILABILITY_REPLACED, _changed_reason("Page loaded but title changed", original_title, current.title)
    if company_mismatch:
        return AVAILABILITY_REPLACED, _changed_reason("Page loaded but company changed", original_company, current.company)
    if content_mismatch and not strong_identity_match:
        return AVAILABILITY_REPLACED, "Page loaded but content no longer matches the stored job fingerprint"
    if strong_identity_match:
        return AVAILABILITY_UNKNOWN, "Stored title/company match, but no apply action was detected"
    if current.apply_exists:
        return AVAILABILITY_UNKNOWN, "Apply action exists, but stored title/company could not be verified"
    if result.redirected:
        return AVAILABILITY_UNKNOWN, f"Job URL redirected to {result.final_url}, but job identity could not be verified"
    if original_location and current.location and not _strong_text_match(original_location, current.location):
        return AVAILABILITY_REPLACED, _changed_reason("Page loaded but location changed", original_location, current.location)
    if original_salary and current.salary and not _strong_text_match(original_salary, current.salary):
        return AVAILABILITY_REPLACED, _changed_reason("Page loaded but salary changed", original_salary, current.salary)
    return AVAILABILITY_UNKNOWN, "Could not verify stored title/company or apply action on the current page"


def _extract_current_job_page(html: str, url: str) -> CurrentJobPage:
    text = _page_text(html)
    soup = BeautifulSoup(html or "", "html.parser")
    try:
        parsed = parse_job_detail(html or "", url)
    except TypeError:
        parsed = None
    salary = _select_text(soup, [".salary", ".job-salary", "[data-testid*=salary]"]) or _regex_value(
        text, r"(?:salary|rate)\s*[:\-]\s*([^\n\r]+)"
    )
    selected_title = _select_text(
        soup,
        [
            "[aria-selected=true] .job-title",
            ".selected .job-title",
            ".selected h1",
            ".jobdetails h1",
            "#td_jobpositionnolink",
            "h1",
            ".job-title",
            ".jobTitle",
            "[data-testid*=title]",
        ],
    )
    selected_company = _select_text(
        soup,
        [
            "[aria-selected=true] .company",
            ".selected .company",
            ".jobdetails .company",
            ".company",
            ".recruiter",
            ".job-company",
            "[data-testid*=company]",
        ],
    )
    title = selected_title or (parsed.title if parsed else None)
    company = selected_company or (parsed.company_name if parsed else None)
    return CurrentJobPage(
        title=title,
        company=company,
        location=parsed.location if parsed else None,
        salary=salary,
        body_text=text,
        content_hash=content_hash(text) if text else None,
        apply_exists=_has_apply_action(html),
        is_jobserve_results_page=_is_jobserve_results_page(html, url),
    )


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


def _is_jobserve_results_page(html: str, url: str) -> bool:
    parsed = urlparse(url)
    if "jobserve.com" not in parsed.netloc.lower():
        return False
    if "jobsearch.aspx" in parsed.path.lower():
        return True
    soup = BeautifulSoup(html or "", "html.parser")
    return soup.find("input", id="jobIDs") is not None or soup.find("input", attrs={"name": "ctl00$main$jobIDs"}) is not None


def _contains_tokenised_text(page_text: str, expected: str | None) -> bool:
    if not page_text or not expected:
        return False
    page = _normalise_words(page_text)
    target = _normalise_words(expected)
    return bool(target and target in page)


def _strong_text_match(expected: str | None, current: str | None) -> bool:
    expected_words = _word_set(expected)
    current_words = _word_set(current)
    if not expected_words or not current_words:
        return False
    expected_norm = " ".join(sorted(expected_words))
    current_norm = " ".join(sorted(current_words))
    if expected_norm == current_norm:
        return True
    overlap = len(expected_words & current_words) / max(len(expected_words), 1)
    return overlap >= 0.75 and len(expected_words & current_words) >= min(2, len(expected_words))


def _optional_strong_match(expected: str | None, current: str | None) -> bool:
    if not expected:
        return True
    return _strong_text_match(expected, current)


def _content_mismatch(
    original_hash: str | None,
    current_hash: str | None,
    original_text: str | None,
    current_text: str,
) -> bool:
    if original_hash and current_hash and original_hash == current_hash:
        return False
    original_words = _word_set(original_text)
    current_words = _word_set(current_text)
    if len(original_words) < 8 or len(current_words) < 8:
        return False
    overlap = len(original_words & current_words) / max(len(original_words), 1)
    return overlap < 0.25


def _changed_reason(prefix: str, original: str | None, current: str | None) -> str:
    return f"{prefix} from {original or 'unknown'} to {current or 'unknown'}"


def _select_text(soup: BeautifulSoup, selectors: list[str]) -> str | None:
    for selector in selectors:
        found = soup.select_one(selector)
        if found:
            text = " ".join(found.get_text(" ", strip=True).split())
            if text:
                return text
    return None


def _regex_value(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return " ".join(match.group(1).split()) if match else None


def _word_set(value: str | None) -> set[str]:
    if not value:
        return set()
    stopwords = {"the", "and", "or", "a", "an", "to", "of", "for", "in", "on", "with", "at", "by"}
    return {word for word in re.findall(r"[a-z0-9]+", value.lower()) if len(word) > 1 and word not in stopwords}


def _normalise_words(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value.lower())).strip()


def _normalise_url(value: str | None) -> str:
    if not value:
        return ""
    parsed = urlparse(value)
    path = parsed.path.rstrip("/")
    return parsed._replace(path=path, fragment="").geturl()
