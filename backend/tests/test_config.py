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
