import re
from dataclasses import dataclass, field
from urllib.parse import urldefrag, urljoin, urlsplit

from bs4 import BeautifulSoup


DEFAULT_JOB_URL_PATTERNS = [
    r"/jobs?/",
    r"^/g[a-z0-9]{2,}",
    r"jobserve\.com/g[a-z0-9]{2,}",
    r"/careers?/",
    r"/vacancies?/",
    r"/openings?/",
    r"/roles?/",
    r"/positions?/",
    r"[?&](?:jobid|job_id|jobref|job_ref|jid|job)=?",
]

DEFAULT_DENY_PATTERNS = [
    r"/login",
    r"/sign-?up",
    r"/privacy",
    r"/terms",
    r"/cookies?",
    r"facebook\.com",
    r"twitter\.com",
    r"x\.com",
    r"linkedin\.com",
    r"instagram\.com",
    r"mailto:",
    r"tel:",
    r"\.(?:png|jpe?g|gif|svg|webp|pdf|zip|docx?|xlsx?)(?:$|\?)",
]


@dataclass(frozen=True)
class DiscoveredLink:
    href: str
    url: str


@dataclass(frozen=True)
class DiscoveredLinks:
    links: list[str]
    likely_job_links: list[str]
    warnings: list[str]
    link_items: list[DiscoveredLink] = field(default_factory=list)
    likely_link_items: list[DiscoveredLink] = field(default_factory=list)


def discover_links(
    html: str,
    base_url: str,
    *,
    same_domain_only: bool = True,
    allow_patterns: list[str] | None = None,
    deny_patterns: list[str] | None = None,
    job_link_patterns: list[str] | None = None,
) -> DiscoveredLinks:
    base_domain = urlsplit(base_url).netloc.lower()
    warnings: list[str] = []
    seen: set[str] = set()
    link_items: list[DiscoveredLink] = []
    soup = BeautifulSoup(html, "html.parser")
    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href") or "").strip()
        url = normalise_discovered_url(urljoin(base_url, href))
        if not url or url in seen:
            continue
        if same_domain_only and urlsplit(url).netloc.lower() != base_domain:
            continue
        if _matches_any_candidate([href, url], deny_patterns or DEFAULT_DENY_PATTERNS):
            continue
        if allow_patterns and not _matches_any_candidate([href, urlsplit(url).path, url], allow_patterns):
            continue
        seen.add(url)
        link_items.append(DiscoveredLink(href=href, url=url))

    likely_patterns = job_link_patterns or DEFAULT_JOB_URL_PATTERNS
    likely_items = [item for item in link_items if is_likely_job_link(item.href, item.url, likely_patterns)]
    if any("linkedin.com" in item.url.lower() for item in likely_items):
        warnings.append("LinkedIn URLs were excluded from scraping.")
        likely_items = [item for item in likely_items if "linkedin.com" not in item.url.lower()]
    links = [item.url for item in link_items]
    likely = [item.url for item in likely_items]
    return DiscoveredLinks(
        links=links,
        likely_job_links=likely,
        warnings=warnings,
        link_items=link_items,
        likely_link_items=likely_items,
    )


def normalise_discovered_url(url: str) -> str:
    if not url:
        return ""
    without_fragment = urldefrag(url.strip())[0]
    parts = urlsplit(without_fragment)
    if parts.scheme not in {"http", "https"}:
        return ""
    return parts._replace(scheme=parts.scheme.lower(), netloc=parts.netloc.lower()).geturl().rstrip("/")


def is_likely_job_url(url: str, patterns: list[str] | None = None) -> bool:
    return is_likely_job_link(url, url, patterns)


def is_likely_job_link(href: str, url: str, patterns: list[str] | None = None) -> bool:
    candidates = [href.lower(), url.lower()]
    if _matches_any_candidate(candidates, DEFAULT_DENY_PATTERNS):
        return False
    return _matches_any_candidate(candidates, patterns or DEFAULT_JOB_URL_PATTERNS)


def _matches_any(value: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        try:
            if re.search(pattern, value, flags=re.IGNORECASE):
                return True
        except re.error:
            if pattern.lower() in value.lower():
                return True
    return False


def _matches_any_candidate(values: list[str], patterns: list[str]) -> bool:
    return any(_matches_any(value, patterns) for value in values if value)
