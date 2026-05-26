from app.config import Settings


def test_cors_origins_accept_comma_separated_values() -> None:
    settings = Settings(
        CORS_ORIGINS=(
            "http://localhost:3000,"
            "http://127.0.0.1:3000,"
            "https://job-intelligence-platform-drab.vercel.app"
        )
    )

    assert settings.cors_origin_list == [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "https://job-intelligence-platform-drab.vercel.app",
    ]


def test_cors_origins_accept_json_list_values() -> None:
    settings = Settings(CORS_ORIGINS='["https://example.com/", "https://app.example.com"]')

    assert settings.cors_origin_list == ["https://example.com", "https://app.example.com"]


def test_default_cors_origins_include_deployed_frontend() -> None:
    settings = Settings(_env_file=None)

    assert "https://job-intelligence-platform-drab.vercel.app" in settings.cors_origin_list


def test_ai_provider_defaults_to_groq() -> None:
    settings = Settings(_env_file=None)

    assert settings.ai_provider == "groq"
    assert settings.ai_model == "llama-3.1-8b-instant"
    assert settings.groq_api_key == ""


def test_browser_timeout_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.scrape_timeout_seconds == 600
    assert settings.apply_timeout_seconds == 900
    assert settings.playwright_step_timeout_ms == 30000
    assert settings.page_navigation_timeout_ms == 60000
    assert settings.rq_result_ttl_seconds == 3600
    assert settings.rq_failure_ttl_seconds == 86400
