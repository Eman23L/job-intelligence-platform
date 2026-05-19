from pathlib import Path
from decimal import Decimal

from app.scrapers.parsers.job_detail import parse_job_detail


FIXTURES = Path(__file__).parent / "fixtures"


def test_json_ld_job_posting_parser() -> None:
    html = (FIXTURES / "generic_job_jsonld.html").read_text(encoding="utf-8")

    parsed = parse_job_detail(html, "https://example.invalid/jobs/senior-data-engineer")

    assert parsed.title == "Senior Data Engineer"
    assert parsed.company_name == "Example Data Ltd"
    assert parsed.location == "London, GB"
    assert parsed.salary_min == 65000
    assert parsed.salary_max == 85000
    assert parsed.salary_currency == "GBP"
    assert "Python and SQL" in parsed.description_text


def test_common_html_job_parser() -> None:
    html = (FIXTURES / "generic_job_html.html").read_text(encoding="utf-8")

    parsed = parse_job_detail(html, "https://example.invalid/careers/analytics-engineer")

    assert parsed.title == "Analytics Engineer"
    assert parsed.company_name == "Example Analytics Ltd"
    assert parsed.remote_type == "remote"
    assert parsed.salary_min == Decimal("50000")
    assert parsed.application_url == "https://example.invalid/apply/analytics-engineer"
