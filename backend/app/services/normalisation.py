import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from urllib.parse import urlsplit, urlunsplit

from app.scrapers.parsers.dates import parse_posted_date
from app.scrapers.parsers.salary import normalise_annual_salary, parse_salary
from app.scrapers.utils.hashing import content_hash


ROLE_FAMILIES = [
    "Data Engineer",
    "Python Data Engineer",
    "Data Platform Engineer",
    "Analytics Engineer",
    "Data & Automation Engineer",
    "Workflow Automation Engineer",
    "Process Automation Engineer",
    "Internal Tools Engineer",
    "Full Stack Automation Engineer",
    "AI Automation Engineer",
    "Technical Consultant Data Automation",
    "Digital Transformation Consultant",
    "Other",
]


@dataclass(frozen=True)
class NormalisedJob:
    title: str
    company_name: str | None
    location: str | None
    remote_type: str | None
    employment_type: str | None
    salary_min: Decimal | None
    salary_max: Decimal | None
    salary_currency: str | None
    salary_min_raw: Decimal | None
    salary_max_raw: Decimal | None
    salary_period: str | None
    normalized_annual_min: Decimal | None
    normalized_annual_max: Decimal | None
    posted_at: datetime | None
    role_family: str
    seniority_level: str | None
    canonical_url: str
    content_hash: str


def normalise_job_fields(
    *,
    title: str,
    company_name: str | None = None,
    location: str | None = None,
    remote_type: str | None = None,
    employment_type: str | None = None,
    salary_text: str | None = None,
    salary_min: Decimal | None = None,
    salary_max: Decimal | None = None,
    salary_currency: str | None = None,
    salary_min_raw: Decimal | None = None,
    salary_max_raw: Decimal | None = None,
    salary_period: str | None = None,
    normalized_annual_min: Decimal | None = None,
    normalized_annual_max: Decimal | None = None,
    posted_date: str | datetime | None = None,
    canonical_url: str = "",
    description_text: str | None = None,
) -> NormalisedJob:
    cleaned_title = normalise_title(title)
    cleaned_company = normalise_company_name(company_name)
    cleaned_location = normalise_location(location)
    cleaned_url = normalise_canonical_url(canonical_url)
    parsed_salary = parse_salary(salary_text or "") if salary_text else {}
    resolved_salary_min = salary_min or parsed_salary.get("salary_min")
    resolved_salary_max = salary_max or parsed_salary.get("salary_max")
    resolved_currency = salary_currency or parsed_salary.get("salary_currency")
    resolved_raw_min = salary_min_raw or parsed_salary.get("salary_min_raw") or resolved_salary_min
    resolved_raw_max = salary_max_raw or parsed_salary.get("salary_max_raw") or resolved_salary_max
    resolved_period = salary_period or parsed_salary.get("salary_period")
    annual_min = normalized_annual_min or parsed_salary.get("normalized_annual_min")
    annual_max = normalized_annual_max or parsed_salary.get("normalized_annual_max")
    if annual_min is None and annual_max is None:
        annual_min, annual_max = normalise_annual_salary(resolved_raw_min, resolved_raw_max, resolved_period)
    posted_at = posted_date if isinstance(posted_date, datetime) else parse_posted_date(posted_date or "")
    content = "|".join([cleaned_title, cleaned_company or "", cleaned_location or "", description_text or ""])

    return NormalisedJob(
        title=cleaned_title,
        company_name=cleaned_company,
        location=cleaned_location,
        remote_type=normalise_remote_type(remote_type, cleaned_location, description_text),
        employment_type=normalise_employment_type(employment_type, f"{cleaned_title} {description_text or ''}"),
        salary_min=resolved_salary_min,
        salary_max=resolved_salary_max,
        salary_currency=resolved_currency if isinstance(resolved_currency, str) else None,
        salary_min_raw=resolved_raw_min,
        salary_max_raw=resolved_raw_max,
        salary_period=resolved_period if isinstance(resolved_period, str) else None,
        normalized_annual_min=annual_min,
        normalized_annual_max=annual_max,
        posted_at=posted_at,
        role_family=classify_role_family(cleaned_title, description_text or ""),
        seniority_level=detect_seniority(cleaned_title, description_text or ""),
        canonical_url=cleaned_url,
        content_hash=content_hash(content),
    )


def normalise_title(title: str) -> str:
    text = re.sub(r"\s+", " ", title).strip(" -|\t\r\n")
    replacements = {
        "sr.": "Senior",
        "jr.": "Junior",
        "dev": "Developer",
        "eng": "Engineer",
    }
    words = [replacements.get(word.lower(), word) for word in text.split()]
    return " ".join(words)


def normalise_company_name(company_name: str | None) -> str | None:
    if not company_name:
        return None
    return re.sub(r"\s+", " ", company_name).strip()


def normalise_location(location: str | None) -> str | None:
    if not location:
        return None
    return re.sub(r"\s+", " ", location).strip()


def normalise_remote_type(remote_type: str | None, location: str | None, description: str | None) -> str | None:
    text = " ".join(part for part in [remote_type, location, description] if part).lower()
    if "hybrid" in text:
        return "hybrid"
    if "remote" in text or "work from home" in text:
        return "remote"
    if "on-site" in text or "onsite" in text or "office based" in text:
        return "onsite"
    return remote_type


def normalise_employment_type(employment_type: str | None, text: str) -> str | None:
    source = " ".join(part for part in [employment_type, text] if part).lower()
    if "contract" in source:
        return "contract"
    if "part time" in source or "part-time" in source:
        return "part_time"
    if "permanent" in source or "full time" in source or "full-time" in source:
        return "full_time"
    return employment_type


def normalise_canonical_url(url: str) -> str:
    if not url:
        return url
    parts = urlsplit(url.strip())
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/") or "/", "", ""))


def classify_role_family(title: str, description: str = "") -> str:
    title_text = title.lower()
    text = f"{title} {description}".lower()
    checks = [
        ("Technical Consultant Data Automation", ["consultant", "data", "automation"]),
        ("Digital Transformation Consultant", ["digital transformation", "consultant"]),
        ("AI Automation Engineer", ["ai", "automation"]),
        ("Full Stack Automation Engineer", ["full stack", "automation"]),
        ("Internal Tools Engineer", ["internal tools"]),
        ("Workflow Automation Engineer", ["workflow automation"]),
        ("Process Automation Engineer", ["process automation"]),
        ("Data & Automation Engineer", ["data", "automation"]),
        ("Data Platform Engineer", ["data platform"]),
        ("Analytics Engineer", ["analytics engineer"]),
    ]
    for family, required_terms in checks:
        if all(term in text for term in required_terms):
            return family
    if "python" in title_text and "data engineer" in title_text:
        return "Python Data Engineer"
    if "data engineer" in title_text or ("data pipelines" in text and ("etl" in text or "elt" in text)):
        return "Data Engineer"
    return "Other"


def detect_seniority(title: str, description: str = "") -> str | None:
    text = f"{title} {description}".lower()
    if re.search(r"\b(principal|lead|head of|staff)\b", text):
        return "lead"
    if re.search(r"\b(senior|sr\.)\b", text):
        return "senior"
    if re.search(r"\b(mid|intermediate)\b", text):
        return "mid"
    if re.search(r"\b(junior|jr\.|graduate|entry level)\b", text):
        return "junior"
    return None
