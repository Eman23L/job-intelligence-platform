from app.services.link_discovery import discover_links, is_likely_job_url, normalise_discovered_url


def test_link_discovery_filters_and_identifies_job_links() -> None:
    html = """
    <a href="/jobs/data-engineer#top">Job</a>
    <a href="/privacy">Privacy</a>
    <a href="https://other.invalid/jobs/external">External</a>
    <a href="https://linkedin.com/jobs/view/1">LinkedIn</a>
    """

    result = discover_links(html, "https://example.invalid/careers")

    assert result.links == ["https://example.invalid/jobs/data-engineer"]
    assert result.likely_job_links == ["https://example.invalid/jobs/data-engineer"]


def test_link_discovery_supports_allow_and_custom_job_patterns() -> None:
    html = """
    <a href="/teams/data-engineer">Team page</a>
    <a href="/roles/data-engineer">Role page</a>
    <a href="/blog/data-engineer">Blog</a>
    """

    result = discover_links(
        html,
        "https://example.invalid/careers",
        allow_patterns=[r"/roles/"],
        job_link_patterns=[r"/roles/"],
    )

    assert result.links == ["https://example.invalid/roles/data-engineer"]
    assert result.likely_job_links == ["https://example.invalid/roles/data-engineer"]


def test_url_normalisation_and_likely_job_detection() -> None:
    assert normalise_discovered_url("HTTPS://Example.Invalid/jobs/123#apply") == "https://example.invalid/jobs/123"
    assert is_likely_job_url("https://example.invalid/vacancies/data-engineer")
    assert not is_likely_job_url("https://example.invalid/privacy")


def test_link_discovery_identifies_jobserve_relative_short_links() -> None:
    html = """
    <a href="/gb/en/JobSearch.aspx?shid=634D7F54BB4124FA4D7D">Search</a>
    <a href="/gD8DF">Data engineer</a>
    <a href="/g9ABC?src=search">Platform engineer</a>
    <a href="https://www.jobserve.com/g1234">Cloud engineer</a>
    """

    result = discover_links(html, "https://www.jobserve.com/gb/en/JobSearch.aspx?shid=634D7F54BB4124FA4D7D")

    assert result.links == [
        "https://www.jobserve.com/gb/en/JobSearch.aspx?shid=634D7F54BB4124FA4D7D",
        "https://www.jobserve.com/gD8DF",
        "https://www.jobserve.com/g9ABC?src=search",
        "https://www.jobserve.com/g1234",
    ]
    assert result.likely_job_links == [
        "https://www.jobserve.com/gD8DF",
        "https://www.jobserve.com/g9ABC?src=search",
        "https://www.jobserve.com/g1234",
    ]
    assert result.likely_link_items[0].href == "/gD8DF"
    assert result.likely_link_items[0].url == "https://www.jobserve.com/gD8DF"


def test_link_discovery_matches_custom_patterns_against_raw_href_and_normalized_url() -> None:
    html = """
    <a href="/gD8DF">Raw href match</a>
    <a href="/open?jobid=123">Query job page</a>
    """

    raw_match = discover_links(
        html,
        "https://www.jobserve.com/gb/en/JobSearch.aspx?shid=634D7F54BB4124FA4D7D",
        job_link_patterns=[r"^/g"],
    )
    normalized_match = discover_links(
        html,
        "https://www.jobserve.com/gb/en/JobSearch.aspx?shid=634D7F54BB4124FA4D7D",
        job_link_patterns=[r"jobserve\.com/g"],
    )

    assert raw_match.likely_job_links == ["https://www.jobserve.com/gD8DF"]
    assert normalized_match.likely_job_links == ["https://www.jobserve.com/gD8DF"]
    assert is_likely_job_url("https://www.jobserve.com/open?jobid=123")
