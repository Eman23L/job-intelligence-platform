from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from app.db.models import JobSource
from app.scrapers.parsers.job_detail import ParsedJobDetail, parse_job_detail
from app.scrapers.utils.hashing import content_hash
from app.services.link_discovery import discover_links
from app.services.normalisation import normalise_job_fields


@dataclass(frozen=True)
class JobDiscoveryResult:
    job_urls: list[str]
    discovered_job_ids: list[str]
    warnings: list[str]


class GenericSourceAdapter:
    def __init__(self, source: JobSource) -> None:
        self.source = source

    def discover_job_urls(self, html: str, page_url: str) -> list[str]:
        links = discover_links(
            html,
            page_url,
            allow_patterns=self.source.allowed_path_patterns,
            job_link_patterns=self.source.job_link_patterns,
        )
        return links.likely_job_links

    def discover_jobs(self, html: str, page_url: str) -> JobDiscoveryResult:
        return JobDiscoveryResult(
            job_urls=self.discover_job_urls(html, page_url),
            discovered_job_ids=[],
            warnings=[],
        )

    def parse_job_detail(self, html: str, url: str) -> ParsedJobDetail:
        return parse_job_detail(html, url)

    def normalise(self, parsed: ParsedJobDetail, url: str) -> dict[str, Any]:
        title = parsed.title or "Untitled job"
        description = parsed.description_text or ""
        normalised = normalise_job_fields(
            title=title,
            company_name=parsed.company_name,
            location=parsed.location,
            remote_type=parsed.remote_type,
            employment_type=parsed.employment_type,
            salary_min=parsed.salary_min,
            salary_max=parsed.salary_max,
            salary_currency=parsed.salary_currency,
            salary_min_raw=parsed.salary_min,
            salary_max_raw=parsed.salary_max,
            posted_date=parsed.posted_at,
            canonical_url=parsed.canonical_url or url,
            description_text=description,
        )
        return {
            "source_id": self.source.id,
            "source_job_id": _source_job_id(parsed.canonical_url or url),
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
            "expires_at": parsed.expires_at,
            "status": "active",
            "content_hash": normalised.content_hash,
        }


def _source_job_id(url: str) -> str:
    parts = urlsplit(url)
    stable = f"{parts.netloc}{parts.path}".strip("/") or url
    if len(stable) <= 255:
        return stable
    return content_hash(stable)
