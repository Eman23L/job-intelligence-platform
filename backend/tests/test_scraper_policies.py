from app.scrapers.policies.rate_limits import RateLimiter
from app.scrapers.policies.robots import check_robots_allowed


def test_robots_checker_allows_permitted_path() -> None:
    result = check_robots_allowed(
        "https://example.invalid/robots.txt",
        "https://example.invalid/jobs/123",
        fetcher=lambda _: "User-agent: *\nAllow: /jobs\nDisallow: /private\n",
    )

    assert result.allowed is True


def test_robots_checker_disallows_blocked_path() -> None:
    result = check_robots_allowed(
        "https://example.invalid/robots.txt",
        "https://example.invalid/private/123",
        fetcher=lambda _: "User-agent: *\nDisallow: /private\n",
    )

    assert result.allowed is False


def test_robots_checker_fails_closed_when_unavailable() -> None:
    def broken_fetcher(url: str) -> str:
        raise OSError("offline")

    result = check_robots_allowed(
        "https://example.invalid/robots.txt",
        "https://example.invalid/jobs/123",
        fetcher=broken_fetcher,
    )

    assert result.allowed is False
    assert "could not be checked" in result.reason


def test_rate_limiter_delay_calculation() -> None:
    limiter = RateLimiter(rate_limit_per_minute=30, last_request_at=10.0)

    assert limiter.delay_seconds == 2.0
    assert limiter.delay_until_next(now=11.25) == 0.75
    assert limiter.delay_until_next(now=12.5) == 0.0
