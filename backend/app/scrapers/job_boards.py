import csv
import json
import logging
import random
import threading
import time
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


LOGGER = logging.getLogger(__name__)

DEFAULT_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]


@dataclass(frozen=True)
class JobRecord:
    title: str | None = None
    recruiter: str | None = None
    location: str | None = None
    salary: str | None = None
    employment_type: str | None = None
    description: str | None = None
    skills: list[str] = field(default_factory=list)
    url: str | None = None
    posted_date: str | None = None
    apply_link: str | None = None
    contact_info: dict[str, Any] = field(default_factory=dict)
    json_ld: list[dict[str, Any]] = field(default_factory=list)
    source_job_id: str | None = None


class RateLimiter:
    def __init__(self, min_delay_seconds: float) -> None:
        self.min_delay_seconds = max(0.0, float(min_delay_seconds))
        self._lock = threading.Lock()
        self._last_request_at = 0.0

    def wait(self) -> None:
        if self.min_delay_seconds <= 0:
            return
        with self._lock:
            now = time.monotonic()
            wait_for = self.min_delay_seconds - (now - self._last_request_at)
            if wait_for > 0:
                time.sleep(wait_for)
            self._last_request_at = time.monotonic()


def build_retrying_session(
    *,
    total_retries: int = 3,
    backoff_factor: float = 0.6,
    status_forcelist: tuple[int, ...] = (429, 500, 502, 503, 504),
) -> requests.Session:
    retry = Retry(
        total=total_retries,
        connect=total_retries,
        read=total_retries,
        status=total_retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,
        allowed_methods=frozenset({"GET", "POST"}),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


class BaseJobBoardAdapter(ABC):
    source_name: str

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        user_agents: list[str] | None = None,
        min_delay_seconds: float = 1.0,
        timeout_seconds: float = 20.0,
    ) -> None:
        self.session = session or build_retrying_session()
        self.user_agents = user_agents or DEFAULT_USER_AGENTS
        self.rate_limiter = RateLimiter(min_delay_seconds)
        self.timeout_seconds = timeout_seconds

    @abstractmethod
    def fetch_search_page(self, url: str) -> str:
        raise NotImplementedError

    @abstractmethod
    def extract_job_ids(self, html: str) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def discover_search_pages(self, first_url: str, *, max_pages: int) -> list[tuple[str, str]]:
        raise NotImplementedError

    @abstractmethod
    def fetch_job_detail_payload(self, job_id: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def parse_job_detail(self, payload: dict[str, Any], *, job_id: str) -> JobRecord:
        raise NotImplementedError

    def scrape(self, search_url: str, *, max_pages: int = 1, max_jobs: int | None = None, max_workers: int = 8) -> list[JobRecord]:
        pages = self.discover_search_pages(search_url, max_pages=max_pages)
        job_ids = _dedupe(id_ for _, html in pages for id_ in self.extract_job_ids(html))
        if max_jobs is not None:
            job_ids = job_ids[: max(0, max_jobs)]
        LOGGER.info("discovered %s %s job IDs across %s pages", len(job_ids), self.source_name, len(pages))
        return self.fetch_job_details(job_ids, max_workers=max_workers)

    def fetch_job_details(self, job_ids: list[str], *, max_workers: int = 8) -> list[JobRecord]:
        records: list[JobRecord] = []
        workers = max(1, min(max_workers, len(job_ids) or 1))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_id = {executor.submit(self._fetch_and_parse_job, job_id): job_id for job_id in job_ids}
            for future in as_completed(future_to_id):
                job_id = future_to_id[future]
                try:
                    record = future.result()
                except Exception as exc:  # noqa: BLE001 - individual job failures should not stop a run.
                    LOGGER.warning("failed to fetch %s job_id=%s error=%s", self.source_name, job_id, exc)
                    continue
                records.append(record)
        return dedupe_records(records)

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        self.rate_limiter.wait()
        headers = dict(kwargs.pop("headers", {}) or {})
        headers.setdefault("User-Agent", random.choice(self.user_agents))
        timeout = kwargs.pop("timeout", self.timeout_seconds)
        raise_for_status = kwargs.pop("raise_for_status", True)
        response = self.session.request(method, url, headers=headers, timeout=timeout, **kwargs)
        if raise_for_status:
            response.raise_for_status()
        return response

    def _fetch_and_parse_job(self, job_id: str) -> JobRecord:
        payload = self.fetch_job_detail_payload(job_id)
        return self.parse_job_detail(payload, job_id=job_id)


def dedupe_records(records: Iterable[JobRecord]) -> list[JobRecord]:
    seen: set[str] = set()
    output: list[JobRecord] = []
    for record in records:
        key = record.source_job_id or record.url or f"{record.title}|{record.recruiter}|{record.location}"
        if key in seen:
            continue
        seen.add(key)
        output.append(record)
    return output


class ScraperOrchestrator:
    def __init__(self, adapter: BaseJobBoardAdapter) -> None:
        self.adapter = adapter

    def run(
        self,
        search_url: str,
        *,
        max_pages: int = 1,
        max_jobs: int | None = None,
        max_workers: int = 8,
    ) -> list[JobRecord]:
        return self.adapter.scrape(
            search_url,
            max_pages=max_pages,
            max_jobs=max_jobs,
            max_workers=max_workers,
        )

    def run_and_export(
        self,
        search_url: str,
        *,
        json_path: str | Path,
        csv_path: str | Path,
        max_pages: int = 1,
        max_jobs: int | None = None,
        max_workers: int = 8,
    ) -> list[JobRecord]:
        records = self.run(search_url, max_pages=max_pages, max_jobs=max_jobs, max_workers=max_workers)
        export_jobs_json(records, json_path)
        export_jobs_csv(records, csv_path)
        return records


def export_jobs_json(records: list[JobRecord], output_path: str | Path) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([asdict(record) for record in records], indent=2, ensure_ascii=False), encoding="utf-8")


def export_jobs_csv(records: list[JobRecord], output_path: str | Path) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "title",
        "recruiter",
        "location",
        "salary",
        "employment_type",
        "description",
        "skills",
        "url",
        "posted_date",
        "apply_link",
        "contact_info",
        "json_ld",
        "source_job_id",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            row = asdict(record)
            row["skills"] = "; ".join(record.skills)
            row["contact_info"] = json.dumps(record.contact_info, ensure_ascii=False)
            row["json_ld"] = json.dumps(record.json_ld, ensure_ascii=False)
            writer.writerow(row)


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output
