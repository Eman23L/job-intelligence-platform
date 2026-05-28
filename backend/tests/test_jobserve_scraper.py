from pathlib import Path

from app.scrapers.jobserve import (
    discover_jobserve_pagination_urls,
    extract_jobserve_detail_html,
    extract_jobserve_job_ids,
    extract_jobserve_visible_results,
    is_jobserve_search_page,
    parse_jobserve_detail_html,
)


FIXTURES = Path(__file__).parent / "fixtures"


def test_jobserve_search_page_detection() -> None:
    assert is_jobserve_search_page("https://www.jobserve.com/gb/en/JobSearch.aspx?shid=634D7F54BB4124FA4D7D")
    assert not is_jobserve_search_page("https://www.jobserve.com/gD8DF")
    assert not is_jobserve_search_page("https://example.invalid/gb/en/JobSearch.aspx")


def test_extract_jobserve_job_ids_from_hidden_input() -> None:
    html = (FIXTURES / "jobserve_search.html").read_text(encoding="utf-8")

    assert extract_jobserve_job_ids(html) == ["D8DF", "A12345", "B67890"]


def test_extract_jobserve_job_ids_falls_back_to_input_name() -> None:
    html = '<input type="hidden" name="ctl00$main$jobIDs" value="ONE#TWO#THREE" />'

    assert extract_jobserve_job_ids(html) == ["ONE", "TWO", "THREE"]


def test_extract_jobserve_job_ids_accepts_multiple_delimiters() -> None:
    html = '<input id="jobIDs" value="ONE,TWO|THREE;FOUR FIVE" />'

    assert extract_jobserve_job_ids(html) == ["ONE", "TWO", "THREE", "FOUR", "FIVE"]


def test_jobserve_scraper_parses_visible_result_list_html() -> None:
    html = """
    <section id="results">
      <article class="job-result" data-jobid="ABC123">
        <a class="job-title" href="/gb/en/search-jobs-in-London/AI-ENGINEER-ABC123/">AI Engineer</a>
        <span class="company">Example Recruiter</span>
      </article>
      <article class="job-result" onclick="javascript:GetJobDetails('DEF456789', 'False')">
        <h3>ML Platform Engineer</h3>
        <span class="recruiter">Another Recruiter</span>
      </article>
    </section>
    """

    visible = extract_jobserve_visible_results(html)

    assert [item["job_id"] for item in visible] == ["ABC123", "DEF456789"]
    assert visible[0]["title"] == "AI Engineer"
    assert visible[0]["company"] == "Example Recruiter"
    assert visible[0]["url"] == "https://www.jobserve.com/gb/en/search-jobs-in-London/AI-ENGINEER-ABC123/"
    assert extract_jobserve_job_ids(html) == ["ABC123", "DEF456789"]


def test_extract_jobserve_detail_html_from_ajax_payload() -> None:
    payload = {"d": {"JobDetailHtml": "<h1>Python Developer</h1>"}}

    assert extract_jobserve_detail_html(payload) == "<h1>Python Developer</h1>"


def test_parse_jobserve_detail_html_extracts_core_fields() -> None:
    html = """
    <article>
      <script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@type": "JobPosting",
          "title": "Python Developer",
          "hiringOrganization": {"name": "Example Recruiter"},
          "jobLocation": {"address": {"addressLocality": "London", "addressCountry": "GB"}},
          "employmentType": "CONTRACT",
          "datePosted": "2026-05-18",
          "url": "/gb/en/job/PY123"
        }
      </script>
      <p>Salary: GBP 500 per day</p>
      <p>Skills: Python, FastAPI, SQL</p>
      <div id="JobDescription">Build data services. Contact: Jane Smith jane@example.com +44 20 1234 5678</div>
      <a href="/Apply/PY123">Apply</a>
    </article>
    """

    record = parse_jobserve_detail_html(html, job_id="PY123")

    assert record.title == "Python Developer"
    assert record.recruiter == "Example Recruiter"
    assert record.location == "London, GB"
    assert record.salary == "GBP 500 per day"
    assert record.employment_type == "CONTRACT"
    assert record.skills == ["Python", "FastAPI", "SQL"]
    assert record.posted_date == "2026-05-18"
    assert record.url == "https://www.jobserve.com/gb/en/job/PY123"
    assert record.apply_link == "https://www.jobserve.com/Apply/PY123"
    assert record.contact_info["emails"] == ["jane@example.com"]


def test_discover_jobserve_pagination_urls() -> None:
    html = """
    <a href="/gb/en/JobSearch.aspx?page=2">Next</a>
    <a href="https://example.invalid/gb/en/JobSearch.aspx?page=3">Next</a>
    <a href="/gb/en/job/PY123">Job</a>
    """

    assert discover_jobserve_pagination_urls(html, "https://www.jobserve.com/gb/en/JobSearch.aspx?page=1") == [
        "https://www.jobserve.com/gb/en/JobSearch.aspx?page=2"
    ]
