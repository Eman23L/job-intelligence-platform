import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any


BLOCKED_JOB_PHRASES = (
    "cookie policy",
    "browser information",
    "why choose",
    "search jobs",
    "find jobs",
    "terms",
    "privacy",
    "sign in",
    "signin",
    "register",
)

NAVIGATION_PHRASES = (
    "home",
    "about us",
    "contact us",
    "cookie",
    "privacy",
    "terms",
    "sign in",
    "register",
    "saved jobs",
    "job alerts",
    "employers",
)

GENERIC_TITLES = {
    "",
    "untitled job",
    "untitled jobserve job",
    "jobserve",
    "jobs",
    "careers",
    "vacancies",
}


@dataclass(frozen=True)
class JobValidationResult:
    is_valid: bool
    reasons: list[str]


def validate_normalised_job(item: dict[str, Any], *, source_name: str | None = None) -> JobValidationResult:
    reasons: list[str] = []
    title = _clean(item.get("title"))
    description = _clean(item.get("description_text"))
    company = _clean(item.get("company_name"))
    location = _clean(item.get("location"))
    salary_min = item.get("salary_min") or item.get("salary_min_raw") or item.get("normalized_annual_min")
    salary_max = item.get("salary_max") or item.get("salary_max_raw") or item.get("normalized_annual_max")
    url = _clean(item.get("canonical_url"))
    source = (source_name or "").lower()

    if not _has_real_title(title):
        reasons.append("missing real job title")

    blocked_target = " ".join(value for value in [title, description, url] if value).lower()
    for phrase in BLOCKED_JOB_PHRASES:
        if phrase in blocked_target:
            reasons.append(f"blocked phrase: {phrase}")

    has_salary = _has_salary(salary_min) or _has_salary(salary_max)
    has_description = bool(description)
    if not any([company, location, has_salary, has_description]):
        reasons.append("missing company, location, salary, and description")

    if has_description and _word_count(description) < 6:
        reasons.append("description is extremely short")

    if has_description and _looks_navigation_heavy(description):
        reasons.append("description appears navigation/footer-heavy")

    if "jobserve" in source and url and "jobserve.com" in url.lower() and "/apply/" not in url.lower() and "/job/" not in url.lower():
        reasons.append("JobServe URL is not a job/apply URL")

    return JobValidationResult(is_valid=not reasons, reasons=reasons)


def _has_real_title(title: str) -> bool:
    lowered = title.lower()
    return bool(title and len(title) >= 4 and lowered not in GENERIC_TITLES)


def _has_salary(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, Decimal):
        return value > 0
    return bool(str(value).strip())


def _looks_navigation_heavy(description: str) -> bool:
    lowered = description.lower()
    words = _word_count(description)
    if words < 12:
        return False
    nav_hits = sum(1 for phrase in NAVIGATION_PHRASES if phrase in lowered)
    lines = [line.strip() for line in re.split(r"[\n\r]+", description) if line.strip()]
    short_lines = sum(1 for line in lines if len(line.split()) <= 4)
    return nav_hits >= 5 and (words < 40 or not lines or short_lines / len(lines) >= 0.45)


def _word_count(value: str) -> int:
    return len(re.findall(r"[A-Za-z0-9]+", value))


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())
