from datetime import datetime, timezone
from decimal import Decimal

from app.scrapers.parsers.dates import parse_posted_date
from app.scrapers.parsers.json_ld import extract_job_postings
from app.scrapers.parsers.salary import parse_salary


def test_salary_parser_extracts_range() -> None:
    result = parse_salary("Salary: £45,000 - 55,000 plus benefits")

    assert result["salary_min"] == Decimal("45000")
    assert result["salary_max"] == Decimal("55000")
    assert result["salary_currency"] == "GBP"


def test_salary_parser_extracts_k_values() -> None:
    result = parse_salary("GBP 35k to 42k")

    assert result["salary_min"] == Decimal("35000")
    assert result["salary_max"] == Decimal("42000")


def test_salary_parser_normalises_day_rates() -> None:
    result = parse_salary("Rate: GBP 500 - 600 per day")

    assert result["salary_min_raw"] == Decimal("500")
    assert result["salary_max_raw"] == Decimal("600")
    assert result["salary_period"] == "day"
    assert result["normalized_annual_min"] == Decimal("115000")
    assert result["normalized_annual_max"] == Decimal("138000")


def test_salary_parser_normalises_hourly_rates() -> None:
    result = parse_salary("£50 per hour")

    assert result["salary_period"] == "hour"
    assert result["normalized_annual_min"] == Decimal("92000")
    assert result["normalized_annual_max"] == Decimal("92000")


def test_date_parser_handles_relative_days() -> None:
    now = datetime(2026, 5, 13, tzinfo=timezone.utc)

    assert parse_posted_date("2 days ago", now=now) == datetime(2026, 5, 11, tzinfo=timezone.utc)


def test_date_parser_handles_iso_date() -> None:
    assert parse_posted_date("2026-05-10") == datetime(2026, 5, 10, tzinfo=timezone.utc)


def test_json_ld_parser_extracts_job_posting() -> None:
    html = """
    <script type="application/ld+json">
      {"@context": "https://schema.org", "@type": "JobPosting", "title": "Analyst"}
    </script>
    """

    jobs = extract_job_postings(html)

    assert len(jobs) == 1
    assert jobs[0]["title"] == "Analyst"
