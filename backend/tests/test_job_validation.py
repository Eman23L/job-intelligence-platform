from app.services.job_validation import validate_normalised_job


def test_rejects_blocked_policy_page_title() -> None:
    result = validate_normalised_job(
        {
            "title": "Website Cookie Policy",
            "company_name": None,
            "location": None,
            "salary_min": None,
            "salary_max": None,
            "description_text": "Cookie settings and privacy controls for this website.",
            "canonical_url": "https://www.jobserve.com/cookie-policy",
        }
    )

    assert not result.is_valid
    assert any("blocked phrase" in reason for reason in result.reasons)


def test_rejects_generic_navigation_heavy_page() -> None:
    result = validate_normalised_job(
        {
            "title": "Find Jobs with JobServe.com",
            "company_name": None,
            "location": None,
            "salary_min": None,
            "salary_max": None,
            "description_text": (
                "Home About us Contact us Cookie policy Privacy Terms Sign in Register "
                "Saved jobs Job alerts Employers Find Jobs Search Jobs"
            ),
            "canonical_url": "https://www.jobserve.com/gb/en/find-jobs",
        }
    )

    assert not result.is_valid
    assert "description appears navigation/footer-heavy" in result.reasons


def test_accepts_real_job_with_minimal_structured_signals() -> None:
    result = validate_normalised_job(
        {
            "title": "Senior Data Engineer",
            "company_name": "Example Recruiter",
            "location": "London",
            "salary_min": None,
            "salary_max": None,
            "description_text": "Build data pipelines with Python and SQL.",
            "canonical_url": "https://example.invalid/jobs/senior-data-engineer",
        }
    )

    assert result.is_valid
