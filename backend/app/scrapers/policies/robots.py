from collections.abc import Callable
from dataclasses import dataclass
from urllib import robotparser
from urllib.error import URLError
from urllib.request import urlopen


@dataclass(frozen=True)
class RobotsCheckResult:
    allowed: bool
    reason: str


RobotsFetcher = Callable[[str], str]


def _default_fetcher(robots_url: str) -> str:
    with urlopen(robots_url, timeout=10) as response:  # noqa: S310 - future live scrapers must pass allowed URLs.
        return response.read().decode("utf-8", errors="replace")


def check_robots_allowed(
    robots_url: str | None,
    target_url: str,
    user_agent: str = "UKJobSearchIntelligenceBot",
    *,
    fail_closed: bool = True,
    fetcher: RobotsFetcher | None = None,
) -> RobotsCheckResult:
    if not robots_url:
        return RobotsCheckResult(False, "robots_url is missing")

    try:
        body = (fetcher or _default_fetcher)(robots_url)
    except (OSError, URLError, TimeoutError) as exc:
        if fail_closed:
            return RobotsCheckResult(False, f"robots.txt could not be checked: {exc}")
        return RobotsCheckResult(True, f"robots.txt could not be checked; fail_closed disabled: {exc}")

    parser = robotparser.RobotFileParser()
    parser.set_url(robots_url)
    parser.parse(body.splitlines())
    allowed = parser.can_fetch(user_agent, target_url)
    reason = "robots.txt permits this URL" if allowed else "robots.txt disallows this URL"
    return RobotsCheckResult(allowed, reason)
