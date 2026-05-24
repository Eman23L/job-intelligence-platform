import json
import logging
import re
from typing import Any
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from app.db.models import JobSource
from app.scrapers.generic import GenericSourceAdapter, JobDiscoveryResult
from app.scrapers.job_boards import BaseJobBoardAdapter, JobRecord
from app.scrapers.parsers.dates import parse_posted_date
from app.scrapers.parsers.html import extract_text
from app.scrapers.parsers.json_ld import extract_job_postings


LOGGER = logging.getLogger(__name__)

JOBSERVE_DETAIL_ENDPOINT = "https://www.jobserve.com/gb/en/JobSearch.aspx/RetrieveSingleJobDetail"
JOBSERVE_FALLBACK_DETAIL_ENDPOINT = "https://www.jobserve.com/WebServices/JobSearch.asmx/RetrieveSingleJobDetail"
JOBSERVE_ROOT = "https://www.jobserve.com"


class JobServeAdapter(BaseJobBoardAdapter):
    source_name = "jobserve"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.detail_referer = JOBSERVE_ROOT + "/gb/en/JobSearch.aspx"

    def fetch_search_page(self, url: str) -> str:
        LOGGER.info("fetching JobServe search page url=%s", url)
        return self.request("GET", url).text

    def extract_job_ids(self, html: str) -> list[str]:
        return extract_jobserve_job_ids(html)

    def discover_search_pages(self, first_url: str, *, max_pages: int) -> list[tuple[str, str]]:
        self.detail_referer = first_url
        max_pages = max(1, max_pages)
        pages: list[tuple[str, str]] = []
        queued = [first_url]
        visited: set[str] = set()
        while queued and len(pages) < max_pages:
            url = queued.pop(0)
            if url in visited:
                continue
            visited.add(url)
            html = self.fetch_search_page(url)
            pages.append((url, html))
            for next_url in discover_jobserve_pagination_urls(html, url):
                if next_url not in visited and next_url not in queued:
                    queued.append(next_url)
        return pages

    def fetch_job_detail_payload(self, job_id: str) -> dict[str, Any]:
        headers = {
            "Content-Type": "application/json; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Origin": JOBSERVE_ROOT,
            "Referer": self.detail_referer,
        }
        LOGGER.info("JobServe detail endpoint request job_id=%s url=%s", job_id, JOBSERVE_DETAIL_ENDPOINT)
        response = self._post_detail_endpoint(JOBSERVE_DETAIL_ENDPOINT, headers=headers, job_id=job_id)
        if response.status_code in {401, 403}:
            LOGGER.warning(
                "JobServe primary detail endpoint rejected job_id=%s status=%s; retrying ASMX detail endpoint",
                job_id,
                response.status_code,
            )
            response = self._post_detail_endpoint(JOBSERVE_FALLBACK_DETAIL_ENDPOINT, headers=headers, job_id=job_id)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError(f"Unexpected JobServe payload type for {job_id}: {type(payload).__name__}")
        return payload

    def _post_detail_endpoint(self, url: str, *, headers: dict[str, str], job_id: str):
        response = self.request("POST", url, headers=headers, json={"id": job_id}, timeout=(5, 10), raise_for_status=False)
        LOGGER.info("JobServe detail endpoint response job_id=%s status=%s url=%s", job_id, response.status_code, url)
        return response

    def parse_job_detail(self, payload: dict[str, Any], *, job_id: str) -> JobRecord:
        html = extract_jobserve_detail_html(payload)
        if not html:
            raise ValueError(f"JobServe detail response did not include d.JobDetailHtml for {job_id}")
        return parse_jobserve_detail_html(html, job_id=job_id)


class JobServeSourceAdapter(GenericSourceAdapter):
    def __init__(self, source: JobSource) -> None:
        super().__init__(source)

    def discover_jobs(self, html: str, page_url: str) -> JobDiscoveryResult:
        generic = super().discover_jobs(html, page_url)
        if not is_jobserve_search_page(page_url):
            return generic

        job_ids = extract_jobserve_job_ids(html)
        if not job_ids:
            return generic

        return JobDiscoveryResult(
            job_urls=generic.job_urls,
            discovered_job_ids=job_ids,
            warnings=[
                "JobServe search page exposed hidden job IDs; use JobServeAdapter for direct detail endpoint scraping.",
            ],
        )


def is_jobserve_search_page(url: str) -> bool:
    parts = urlsplit(url)
    return "jobserve.com" in parts.netloc.lower() and "jobsearch.aspx" in parts.path.lower()


def extract_jobserve_job_ids(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    field = soup.find("input", id="jobIDs")
    if field is None:
        field = soup.find("input", attrs={"name": "ctl00$main$jobIDs"})
    value = str(field.get("value") or "") if field else ""
    seen: set[str] = set()
    job_ids: list[str] = []
    for item in re.split(r"[#%,\|;\s]+", value):
        job_id = item.strip()
        if not job_id or job_id in seen:
            continue
        seen.add(job_id)
        job_ids.append(job_id)
    return job_ids


def discover_jobserve_pagination_urls(html: str, page_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        text = anchor.get_text(" ", strip=True).lower()
        href = str(anchor["href"])
        absolute = urljoin(page_url, href)
        if not is_jobserve_search_page(absolute):
            continue
        if not _looks_like_pagination_link(text, href):
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        urls.append(absolute)
    return urls


def extract_jobserve_detail_html(payload: dict[str, Any]) -> str:
    data = payload.get("d")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return data
    if not isinstance(data, dict):
        return ""
    html = data.get("JobDetailHtml")
    return str(html or "")


def parse_jobserve_detail_html(html: str, *, job_id: str) -> JobRecord:
    soup = BeautifulSoup(html, "html.parser")
    json_ld = extract_job_postings(html)
    json_job = json_ld[0] if json_ld else {}
    text = extract_text(html)
    title = _first_non_empty(
        _json_value(json_job, "title"),
        _select_text(soup, ["h1", ".job-title", ".jobTitle", "[data-testid*=title]", "#td_jobpositionnolink"]),
    )
    recruiter = _first_non_empty(
        _company_from_json_ld(json_job.get("hiringOrganization")),
        _label_value(soup, ["company", "recruiter", "advertiser", "client"]),
        _select_text(soup, [".company", ".recruiter", ".job-company", "[data-testid*=company]"]),
    )
    location = _first_non_empty(
        _location_from_json_ld(json_job.get("jobLocation")),
        _label_value(soup, ["location"]),
        _select_text(soup, [".location", ".job-location", "[data-testid*=location]"]),
    )
    salary = _first_non_empty(
        _salary_from_json_ld(json_job),
        _label_value(soup, ["salary", "rate"]),
        _select_text(soup, [".salary", ".job-salary", "[data-testid*=salary]"]),
        _regex_value(text, r"(?:salary|rate)\s*[:\-]\s*([^\n\r]+)"),
    )
    employment_type = _first_non_empty(
        _employment_from_json_ld(json_job.get("employmentType")),
        _label_value(soup, ["job type", "type", "employment"]),
        _employment_type(text),
    )
    description = _first_non_empty(
        _description_from_json_ld(json_job),
        _select_text(soup, ["#JobDescription", "#jobDescription", ".job-description", ".jobDescription", "[data-testid*=description]"]),
        text,
    )
    posted_date = _first_non_empty(
        _json_value(json_job, "datePosted"),
        _label_value(soup, ["posted", "date posted"]),
        _regex_value(text, r"(?:posted|date posted)\s*[:\-]\s*([^\n\r]+)"),
    )
    normalised_posted = _normalise_date(posted_date)
    job_url = _json_value(json_job, "url")
    if job_url:
        job_url = urljoin(JOBSERVE_ROOT, job_url)
    apply_link = _find_apply_link(soup)
    if apply_link:
        apply_link = urljoin(JOBSERVE_ROOT, apply_link)
    canonical = job_url or apply_link or f"{JOBSERVE_ROOT}/gb/en/job/{job_id}"
    return JobRecord(
        title=title,
        recruiter=recruiter,
        location=location,
        salary=salary,
        employment_type=employment_type,
        description=description,
        skills=extract_skills(soup, text),
        url=canonical,
        posted_date=normalised_posted or posted_date,
        apply_link=apply_link,
        contact_info=extract_contact_info(soup, text),
        json_ld=json_ld,
        source_job_id=job_id,
    )


def extract_skills(soup: BeautifulSoup, text: str) -> list[str]:
    candidates: list[str] = []
    for selector in [".skills li", ".job-skills li", "[data-testid*=skill] li"]:
        for node in soup.select(selector):
            skill = _clean(node.get_text(" ", strip=True))
            if skill:
                candidates.append(skill)
    labelled = _label_value(soup, ["skills", "key skills"])
    if labelled:
        candidates.extend(re.split(r"[,;|]", labelled))
    if not candidates:
        match = re.search(r"(?:skills|required skills|key skills)\s*[:\-]\s*([^\n\r]+)", text, flags=re.IGNORECASE)
        if match:
            candidates.extend(re.split(r"[,;|]", match.group(1)))
    seen: set[str] = set()
    skills: list[str] = []
    for candidate in candidates:
        skill = _clean(candidate)
        if not skill or skill.lower() in seen:
            continue
        seen.add(skill.lower())
        skills.append(skill)
    return skills


def extract_contact_info(soup: BeautifulSoup, text: str) -> dict[str, Any]:
    emails = sorted(set(re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", text)))
    phones = sorted(set(match.strip() for match in re.findall(r"(?:\+?\d[\d\s().-]{7,}\d)", text)))
    contact_name = _first_non_empty(
        _label_value(soup, ["contact", "contact name", "consultant"]),
        _regex_value(text, r"(?:contact|consultant)\s*[:\-]\s*([^\n\r]+)"),
    )
    return {
        "name": contact_name,
        "emails": emails,
        "phones": phones,
    }


def _looks_like_pagination_link(text: str, href: str) -> bool:
    lowered_href = href.lower()
    return (
        text in {"next", ">", ">>"}
        or "next" in text
        or "page=" in lowered_href
        or "pageindex=" in lowered_href
        or "pageoffset=" in lowered_href
    )


def _select_text(soup: BeautifulSoup, selectors: list[str]) -> str | None:
    for selector in selectors:
        found = soup.select_one(selector)
        if found:
            text = _clean(found.get_text(" ", strip=True))
            if text:
                return text
    return None


def _label_value(soup: BeautifulSoup, labels: list[str]) -> str | None:
    label_set = {label.lower() for label in labels}
    for row in soup.find_all(["li", "p", "div", "tr", "span"]):
        row_text = _clean(row.get_text(" ", strip=True))
        if not row_text or ":" not in row_text:
            continue
        label, value = row_text.split(":", 1)
        if label.strip().lower() in label_set:
            return _clean(value)
    for label_node in soup.find_all(string=True):
        label = _clean(label_node)
        if not label or label.rstrip(":").lower() not in label_set:
            continue
        parent = label_node.parent
        sibling = parent.find_next_sibling() if parent else None
        if sibling:
            value = _clean(sibling.get_text(" ", strip=True))
            if value:
                return value
    return None


def _find_apply_link(soup: BeautifulSoup) -> str | None:
    for anchor in soup.find_all("a", href=True):
        text = anchor.get_text(" ", strip=True).lower()
        href = str(anchor["href"])
        if href.lower().startswith("javascript:"):
            continue
        if "apply" in text or "apply" in href.lower():
            return href
    return None


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


def _salary_from_json_ld(item: dict[str, Any]) -> str | None:
    salary = item.get("baseSalary")
    if not isinstance(salary, dict):
        return _clean(item.get("salary"))
    currency = _clean(salary.get("currency")) or _clean(item.get("salaryCurrency"))
    value = salary.get("value")
    if isinstance(value, dict):
        min_value = value.get("minValue") or value.get("value")
        max_value = value.get("maxValue") or value.get("value")
        unit = value.get("unitText")
        pieces = [currency, str(min_value or ""), "-" if min_value and max_value and min_value != max_value else "", str(max_value or ""), str(unit or "")]
        return _clean(" ".join(pieces))
    if value is not None:
        return _clean(" ".join(part for part in [currency, str(value)] if part))
    return None


def _description_from_json_ld(item: dict[str, Any]) -> str | None:
    description = item.get("description")
    if not description:
        return None
    return _clean(BeautifulSoup(str(description), "html.parser").get_text(" ", strip=True))


def _employment_from_json_ld(value: Any) -> str | None:
    if isinstance(value, list):
        return _clean(", ".join(str(item) for item in value if item))
    return _clean(value)


def _employment_type(text: str) -> str | None:
    lowered = text.lower()
    if "contract" in lowered:
        return "contract"
    if "part time" in lowered or "part-time" in lowered:
        return "part_time"
    if "full time" in lowered or "full-time" in lowered or "permanent" in lowered:
        return "full_time"
    if "temporary" in lowered:
        return "temporary"
    return None


def _normalise_date(value: str | None) -> str | None:
    if not value:
        return None
    parsed = parse_posted_date(value)
    return parsed.date().isoformat() if parsed else None


def _json_value(item: dict[str, Any], key: str) -> str | None:
    return _clean(item.get(key))


def _regex_value(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return _clean(match.group(1)) if match else None


def _first_non_empty(*values: str | None) -> str | None:
    for value in values:
        cleaned = _clean(value)
        if cleaned:
            return cleaned
    return None


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None
