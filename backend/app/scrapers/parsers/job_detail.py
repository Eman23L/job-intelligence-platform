from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from app.scrapers.parsers.dates import parse_posted_date
from app.scrapers.parsers.html import extract_text
from app.scrapers.parsers.json_ld import extract_job_postings
from app.scrapers.parsers.salary import parse_salary


@dataclass(frozen=True)
class ParsedJobDetail:
    title: str | None
    company_name: str | None
    location: str | None
    remote_type: str | None
    employment_type: str | None
    salary_min: Any
    salary_max: Any
    salary_currency: str | None
    description_text: str | None
    posted_at: Any
    expires_at: Any
    canonical_url: str
    application_url: str | None
    raw_json: dict[str, Any] | None


def parse_job_detail(html: str, url: str) -> ParsedJobDetail:
    json_jobs = extract_job_postings(html)
    if json_jobs:
        parsed = _from_json_ld(json_jobs[0], url, html)
        if parsed.title and parsed.description_text:
            return parsed
    html_parsed = _from_html(html, url, json_jobs[0] if json_jobs else None)
    if html_parsed.title:
        return html_parsed
    text = extract_text(html)
    return ParsedJobDetail(
        title=_first_line(text),
        company_name=None,
        location=None,
        remote_type=_remote_type(text),
        employment_type=_employment_type(text),
        **parse_salary(text),
        description_text=text or None,
        posted_at=None,
        expires_at=None,
        canonical_url=url,
        application_url=None,
        raw_json=json_jobs[0] if json_jobs else None,
    )


def extract_page_title(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.find("title")
    return _clean(title.get_text(" ", strip=True)) if title else None


def _from_json_ld(item: dict[str, Any], url: str, html: str) -> ParsedJobDetail:
    salary = _salary_from_json_ld(item) or parse_salary(" ".join(str(item.get(key) or "") for key in ("baseSalary", "salaryCurrency")))
    description = BeautifulSoup(str(item.get("description") or ""), "html.parser").get_text(" ", strip=True)
    location = _location_from_json_ld(item.get("jobLocation"))
    canonical = str(item.get("url") or url)
    return ParsedJobDetail(
        title=_clean(item.get("title")),
        company_name=_company_from_json_ld(item.get("hiringOrganization")),
        location=location,
        remote_type=_remote_type(" ".join([str(item.get("jobLocationType") or ""), location or "", description])),
        employment_type=_clean(item.get("employmentType")),
        salary_min=salary["salary_min"],
        salary_max=salary["salary_max"],
        salary_currency=salary["salary_currency"] or _clean(item.get("salaryCurrency")),
        description_text=description or extract_text(html),
        posted_at=parse_posted_date(str(item.get("datePosted") or "")),
        expires_at=parse_posted_date(str(item.get("validThrough") or "")),
        canonical_url=canonical,
        application_url=_clean(item.get("url")) or canonical,
        raw_json=item,
    )


def _from_html(html: str, url: str, raw_json: dict[str, Any] | None) -> ParsedJobDetail:
    soup = BeautifulSoup(html, "html.parser")
    title = _select_text(soup, ["h1", "[data-testid*=title]", ".job-title", ".posting-headline h2", "title"])
    company = _select_text(soup, ["[data-testid*=company]", ".company", ".job-company", ".posting-company"])
    location = _select_text(soup, ["[data-testid*=location]", ".location", ".job-location", ".posting-location"])
    description = _select_text(soup, ["[data-testid*=description]", ".job-description", ".description", "#job-description", "main", "article"])
    text = extract_text(html)
    salary = parse_salary(text)
    canonical = _canonical_url(soup, url)
    return ParsedJobDetail(
        title=title,
        company_name=company,
        location=location,
        remote_type=_remote_type(" ".join([location or "", text])),
        employment_type=_employment_type(text),
        salary_min=salary["salary_min"],
        salary_max=salary["salary_max"],
        salary_currency=salary["salary_currency"],
        description_text=description or text or None,
        posted_at=_date_from_meta(soup, ["datePosted", "article:published_time", "date"]),
        expires_at=_date_from_meta(soup, ["validThrough", "expires"]),
        canonical_url=canonical,
        application_url=_application_url(soup, canonical),
        raw_json=raw_json,
    )


def _select_text(soup: BeautifulSoup, selectors: list[str]) -> str | None:
    for selector in selectors:
        found = soup.select_one(selector)
        if found:
            text = _clean(found.get_text(" ", strip=True))
            if text:
                return text
    return None


def _canonical_url(soup: BeautifulSoup, url: str) -> str:
    link = soup.find("link", rel=lambda value: value and "canonical" in value)
    href = link.get("href") if link else None
    return urljoin(url, str(href)) if href else url


def _application_url(soup: BeautifulSoup, url: str) -> str | None:
    for anchor in soup.find_all("a", href=True):
        text = anchor.get_text(" ", strip=True).lower()
        if "apply" in text:
            return urljoin(url, str(anchor.get("href")))
    return None


def _date_from_meta(soup: BeautifulSoup, names: list[str]):
    for name in names:
        tag = soup.find("meta", attrs={"name": name}) or soup.find("meta", property=name)
        if tag and tag.get("content"):
            return parse_posted_date(str(tag.get("content")))
    return None


def _salary_from_json_ld(item: dict[str, Any]) -> dict[str, Any] | None:
    base = item.get("baseSalary")
    if not isinstance(base, dict):
        return None
    value = base.get("value")
    currency = _clean(base.get("currency")) or _clean(item.get("salaryCurrency"))
    if isinstance(value, dict):
        min_value = value.get("minValue") or value.get("value")
        max_value = value.get("maxValue") or value.get("value") or min_value
        return {"salary_min": min_value, "salary_max": max_value, "salary_currency": currency}
    return {"salary_min": value, "salary_max": value, "salary_currency": currency}


def _location_from_json_ld(value: Any) -> str | None:
    if isinstance(value, list):
        return _location_from_json_ld(value[0]) if value else None
    if not isinstance(value, dict):
        return _clean(value)
    address = value.get("address")
    if isinstance(address, dict):
        parts = [address.get("addressLocality"), address.get("addressRegion"), address.get("addressCountry")]
        return _clean(", ".join(str(part) for part in parts if part))
    return _clean(value.get("name"))


def _company_from_json_ld(value: Any) -> str | None:
    if isinstance(value, dict):
        return _clean(value.get("name"))
    return _clean(value)


def _remote_type(text: str) -> str | None:
    lowered = text.lower()
    if "hybrid" in lowered:
        return "hybrid"
    if "remote" in lowered or "work from home" in lowered:
        return "remote"
    if "onsite" in lowered or "on-site" in lowered:
        return "onsite"
    return None


def _employment_type(text: str) -> str | None:
    lowered = text.lower()
    if "contract" in lowered:
        return "contract"
    if "part time" in lowered or "part-time" in lowered:
        return "part_time"
    if "full time" in lowered or "full-time" in lowered or "permanent" in lowered:
        return "full_time"
    return None


def _first_line(text: str) -> str | None:
    return _clean(text.split(".")[0][:160]) if text else None


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None
