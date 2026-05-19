from decimal import Decimal

from app.services.normalisation import (
    classify_role_family,
    detect_seniority,
    normalise_canonical_url,
    normalise_job_fields,
    normalise_title,
)


def test_title_normalisation_expands_common_abbreviations() -> None:
    assert normalise_title("  Sr. Data Eng  ") == "Senior Data Engineer"


def test_job_field_normalisation() -> None:
    result = normalise_job_fields(
        title="Analytics Engineer",
        company_name=" Example Ltd ",
        location=" Remote UK ",
        salary_text="£45k to 55k",
        canonical_url="HTTPS://Example.Invalid/jobs/123?utm=test",
        description_text="Full-time role building dbt models.",
    )

    assert result.company_name == "Example Ltd"
    assert result.remote_type == "remote"
    assert result.employment_type == "full_time"
    assert result.salary_min == Decimal("45000")
    assert result.salary_max == Decimal("55000")
    assert result.salary_period == "year"
    assert result.normalized_annual_min == Decimal("45000")
    assert result.normalized_annual_max == Decimal("55000")
    assert result.role_family == "Analytics Engineer"
    assert result.canonical_url == "https://example.invalid/jobs/123"


def test_canonical_url_normalisation_removes_query_and_fragment() -> None:
    assert normalise_canonical_url("https://EXAMPLE.invalid/jobs/1?x=1#top") == "https://example.invalid/jobs/1"


def test_role_family_classification() -> None:
    examples = {
        "Data Engineer": "Build ETL data pipelines.",
        "Python Data Engineer": "Python Data Engineer building orchestration.",
        "Data Platform Engineer": "Own the data platform and lakehouse.",
        "Analytics Engineer": "Analytics Engineer using dbt.",
        "Data & Automation Engineer": "Data and automation role.",
        "Workflow Automation Engineer": "Workflow automation engineer.",
        "Process Automation Engineer": "Process automation engineer.",
        "Internal Tools Engineer": "Build internal tools.",
        "Full Stack Automation Engineer": "Full stack automation engineer.",
        "AI Automation Engineer": "AI automation engineer for LLM workflows.",
        "Technical Consultant Data Automation": "Technical consultant for data automation.",
        "Digital Transformation Consultant": "Digital transformation consultant.",
        "Other": "Office Manager",
    }

    for expected, text in examples.items():
        assert classify_role_family(text, text) == expected


def test_seniority_detection() -> None:
    assert detect_seniority("Senior Data Engineer") == "senior"
    assert detect_seniority("Junior Analytics Engineer") == "junior"
    assert detect_seniority("Lead AI Automation Engineer") == "lead"
