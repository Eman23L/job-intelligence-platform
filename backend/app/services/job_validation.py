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
    "sign in",
    "signin",
    "register",
)

POLICY_PAGE_PHRASES = (
    "privacy",
    "privacy policy",
    "terms",
    "terms and conditions",
    "cookie policy",
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
    diagnostics: dict[str, Any] | None = None


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
    positive_signals = _positive_job_signals(item, source_name=source_name)
    privacy_footer_only = _privacy_footer_only(title=title, description=description, url=url)
    diagnostics = {
        "title": title,
        "url": url,
        "positive_signals": positive_signals,
        "privacy_footer_only": privacy_footer_only,
    }

    if not _has_real_title(title):
        reasons.append("missing real job title")

    blocked_target = " ".join(value for value in [title, description, url] if value).lower()
    navigation_target = " ".join(value for value in [title, url] if value).lower()
    for phrase in BLOCKED_JOB_PHRASES:
        target = navigation_target if phrase in {"sign in", "signin", "register"} else blocked_target
        if phrase in target:
            reasons.append(f"blocked phrase: {phrase}")
    for phrase in POLICY_PAGE_PHRASES:
        if _looks_like_policy_page(phrase, title=title, url=url):
            reasons.append(f"blocked policy page: {phrase}")

    has_salary = _has_salary(salary_min) or _has_salary(salary_max)
    has_description = bool(description)
    if not any([company, location, has_salary, has_description]):
        reasons.append("missing company, location, salary, and description")

    if has_description and _word_count(description) < 6:
        reasons.append("description is extremely short")

    if has_description and _looks_navigation_heavy(description) and not _has_strong_jobserve_job_signals(source, positive_signals):
        reasons.append("description appears navigation/footer-heavy")

    if (
        "jobserve" in source
        and url
        and "jobserve.com" in url.lower()
        and "/apply/" not in url.lower()
        and "/job/" not in url.lower()
        and "search-jobs-in" not in url.lower()
    ):
        reasons.append("JobServe URL is not a job/apply URL")

    return JobValidationResult(is_valid=not reasons, reasons=reasons, diagnostics=diagnostics)


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


def _looks_like_policy_page(phrase: str, *, title: str, url: str) -> bool:
    normalized_title = title.lower()
    normalized_url = url.lower()
    if phrase in {"privacy", "terms"}:
        return bool(
            re.search(rf"\b{re.escape(phrase)}\b", normalized_title)
            or re.search(rf"[/_-]{re.escape(phrase)}(?:[/_-]|$)", normalized_url)
            or re.search(rf"\b{re.escape(phrase)}[-_\s]*(?:policy|conditions)\b", normalized_url)
        )
    return phrase in normalized_title or phrase.replace(" ", "-") in normalized_url or phrase.replace(" ", "_") in normalized_url


def _positive_job_signals(item: dict[str, Any], *, source_name: str | None = None) -> dict[str, bool]:
    url = _clean(item.get("canonical_url"))
    source_job_id = _clean(item.get("source_job_id") or item.get("original_external_id"))
    description = _clean(item.get("description_text"))
    source = (source_name or "").lower()
    return {
        "job_title": _has_real_title(_clean(item.get("title"))),
        "company": bool(_clean(item.get("company_name"))),
        "location": bool(_clean(item.get("location"))),
        "salary_or_rate": _has_salary(item.get("salary_min") or item.get("salary_min_raw") or item.get("normalized_annual_min"))
        or _has_salary(item.get("salary_max") or item.get("salary_max_raw") or item.get("normalized_annual_max")),
        "apply_button_text": "apply" in description.lower(),
        "jobserve_reference": bool("jobserve" in source and source_job_id),
        "specific_jobserve_url": bool("jobserve" in source and re.search(r"/(?:job|search-jobs-in|apply)[/-].*[A-Z0-9]{8,}", url, flags=re.I)),
    }


def _has_strong_jobserve_job_signals(source: str, signals: dict[str, bool]) -> bool:
    if "jobserve" not in source:
        return False
    strong_count = sum(
        1
        for key in ["job_title", "company", "location", "salary_or_rate", "apply_button_text", "jobserve_reference", "specific_jobserve_url"]
        if signals.get(key)
    )
    return strong_count >= 3 and signals.get("job_title", False)


def _privacy_footer_only(*, title: str, description: str, url: str) -> bool:
    combined = " ".join(value for value in [description] if value).lower()
    if "privacy" not in combined:
        return False
    return not _looks_like_policy_page("privacy", title=title, url=url)


def _word_count(value: str) -> int:
    return len(re.findall(r"[A-Za-z0-9]+", value))


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())
