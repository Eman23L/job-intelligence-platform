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


def test_rejects_jobserve_marketing_and_browser_pages() -> None:
    for title in ["Browser Information", "Why Choose JobServe?", "Find Jobs with JobServe.com"]:
        result = validate_normalised_job(
            {
                "title": title,
                "company_name": None,
                "location": None,
                "salary_min": None,
                "salary_max": None,
                "description_text": "Generic JobServe website page content.",
                "canonical_url": "https://www.jobserve.com/gb/en/info",
            },
            source_name="JobServe",
        )

        assert not result.is_valid
        assert result.reasons


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


def test_accepts_valid_jobserve_job_with_footer_privacy_text() -> None:
    result = validate_normalised_job(
        {
            "title": "AI Platform Engineer",
            "company_name": "Example Recruiter",
            "location": "London",
            "salary_min_raw": "GBP 600 per day",
            "salary_max": None,
            "description_text": (
                "Apply now for this AI Platform Engineer role building model services. "
                "Footer: Home Contact Us Terms Privacy Cookies Employers."
            ),
            "canonical_url": "https://www.jobserve.com/gb/en/search-jobs-in-London/AI-PLATFORM-ENGINEER-B2640B418B54EFF002/",
            "source_job_id": "B2640B418B54EFF002",
            "original_external_id": "B2640B418B54EFF002",
        },
        source_name="JobServe Search",
    )

    assert result.is_valid
    assert result.diagnostics is not None
    assert result.diagnostics["privacy_footer_only"] is True
    assert result.diagnostics["positive_signals"]["jobserve_reference"] is True
    assert result.diagnostics["positive_signals"]["specific_jobserve_url"] is True


def test_rejects_actual_privacy_policy_page() -> None:
    result = validate_normalised_job(
        {
            "title": "Privacy Policy",
            "company_name": None,
            "location": None,
            "salary_min": None,
            "salary_max": None,
            "description_text": "This page explains how JobServe handles privacy and cookies.",
            "canonical_url": "https://www.jobserve.com/gb/en/privacy-policy",
        },
        source_name="JobServe",
    )

    assert not result.is_valid
    assert any("blocked policy page: privacy" in reason for reason in result.reasons)
    assert result.diagnostics is not None
    assert result.diagnostics["privacy_footer_only"] is False


def test_accepts_valid_jobserve_job_with_apply_button_and_reference() -> None:
    result = validate_normalised_job(
        {
            "title": "AI Architect",
            "company_name": "Example Consulting",
            "location": "London",
            "salary_min": None,
            "salary_max": None,
            "description_text": "AI Architect contract role. Reference: C409B81B016FCBB60F. Apply today.",
            "canonical_url": "https://www.jobserve.com/gb/en/job/C409B81B016FCBB60F",
            "source_job_id": "C409B81B016FCBB60F",
        },
        source_name="JobServe Search",
    )

    assert result.is_valid
    assert result.diagnostics is not None
    assert result.diagnostics["positive_signals"]["apply_button_text"] is True
    assert result.diagnostics["positive_signals"]["jobserve_reference"] is True
