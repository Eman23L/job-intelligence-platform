from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Job, JobAnalysis, JobScore, JobSource, RawJobSnapshot
from app.db.session import get_db
from app.main import app
from app.scrapers.job_boards import JobRecord
from app.scrapers.policies.robots import RobotsCheckResult
from app.schemas.database import JobServeSearchScrapeRequest
from app.services import source_scraping
from scripts.seed_data import seed_database


FIXTURES = Path(__file__).parent / "fixtures"


@contextmanager
def scraping_client() -> Generator[tuple[TestClient, sessionmaker], None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)
    with TestingSession() as db:
        seed_database(db)

    def override_get_db():
        with TestingSession() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    original_session_local = source_scraping.SessionLocal
    source_scraping.SessionLocal = TestingSession
    try:
        yield TestClient(app), TestingSession
    finally:
        source_scraping.SessionLocal = original_session_local
        app.dependency_overrides.clear()


def test_effective_delay_uses_default_and_rate_limit() -> None:
    assert source_scraping.effective_delay_seconds(60, 1) == 8
    assert source_scraping.effective_delay_seconds(2, 8) == 30


def test_source_from_url_and_test_endpoint(monkeypatch) -> None:
    directory_html = (FIXTURES / "generic_directory.html").read_text(encoding="utf-8")

    def fake_fetch(url: str, *, delay_seconds: float = 0):
        return source_scraping.FetchResult(url=url, status_code=200, text=directory_html)

    monkeypatch.setattr(source_scraping, "fetch_url", fake_fetch)
    monkeypatch.setattr(
        source_scraping,
        "check_robots_allowed",
        lambda *args, **kwargs: RobotsCheckResult(True, "robots.txt permits this URL"),
    )

    with scraping_client() as (client, _):
        created = client.post(
            "/sources/from-url",
            json={
                "name": "Fixture Careers",
                "base_url": "https://example.invalid/careers",
                "source_type": "careers",
                "permission_notes": "Reviewed and permitted.",
                "scraping_allowed": True,
                "rate_limit_per_minute": 10,
                "allowed_path_patterns": ["/jobs/", "/careers/"],
                "job_link_patterns": ["/jobs/", "/careers/"],
            },
        )
        assert created.status_code == 201
        source_id = created.json()["id"]

        tested = client.post(f"/sources/{source_id}/test-url", json={"target_url": "https://example.invalid/careers"})

        assert tested.status_code == 200
        body = tested.json()
        assert body["can_fetch"] is True
        assert body["links_found_count"] == 2
        assert body["likely_job_links_count"] == 2


def test_scrape_now_dry_run(monkeypatch) -> None:
    directory_html = (FIXTURES / "generic_directory.html").read_text(encoding="utf-8")

    monkeypatch.setattr(
        source_scraping,
        "fetch_url",
        lambda url, *, delay_seconds=0: source_scraping.FetchResult(url=url, status_code=200, text=directory_html),
    )
    monkeypatch.setattr(
        source_scraping,
        "check_robots_allowed",
        lambda *args, **kwargs: RobotsCheckResult(True, "robots.txt permits this URL"),
    )

    with scraping_client() as (client, _):
        source_id = _create_source(client)
        response = client.post(f"/sources/{source_id}/scrape-now", json={"dry_run": True, "max_jobs": 5})

        assert response.status_code == 200
        started = response.json()
        assert started["status"] == "started"
        status = client.get(f"/scrape-runs/{started['scrape_run_id']}")
        assert status.status_code == 200
        assert status.json()["jobs_found"] == 2
        assert status.json()["jobs_created"] == 0


def test_jobserve_source_test_reports_hidden_job_ids(monkeypatch) -> None:
    jobserve_html = (FIXTURES / "jobserve_search.html").read_text(encoding="utf-8")
    search_url = "https://www.jobserve.com/gb/en/JobSearch.aspx?shid=634D7F54BB4124FA4D7D"

    monkeypatch.setattr(
        source_scraping,
        "fetch_url",
        lambda url, *, delay_seconds=0: source_scraping.FetchResult(url=search_url, status_code=200, text=jobserve_html),
    )
    monkeypatch.setattr(
        source_scraping,
        "check_robots_allowed",
        lambda *args, **kwargs: RobotsCheckResult(True, "robots.txt permits this URL"),
    )

    with scraping_client() as (client, _):
        source_id = _create_jobserve_source(client, search_url)
        response = client.post(f"/sources/{source_id}/test-url", json={"target_url": search_url})

        assert response.status_code == 200
        body = response.json()
        assert body["links_found_count"] == 0
        assert body["likely_job_links_count"] == 3
        assert body["sample_job_links"] == []
        assert body["discovered_job_ids"] == ["D8DF", "A12345", "B67890"]


def test_jobserve_scrape_now_dry_run_counts_hidden_job_ids(monkeypatch) -> None:
    jobserve_html = (FIXTURES / "jobserve_search.html").read_text(encoding="utf-8")
    search_url = "https://www.jobserve.com/gb/en/JobSearch.aspx?shid=634D7F54BB4124FA4D7D"

    monkeypatch.setattr(
        source_scraping,
        "fetch_url",
        lambda url, *, delay_seconds=0: source_scraping.FetchResult(url=search_url, status_code=200, text=jobserve_html),
    )
    monkeypatch.setattr(
        source_scraping,
        "check_robots_allowed",
        lambda *args, **kwargs: RobotsCheckResult(True, "robots.txt permits this URL"),
    )
    monkeypatch.setattr(
        source_scraping,
        "_fetch_jobserve_detail_records",
        lambda job_ids, **kwargs: (
            [
                JobRecord(
                    source_job_id=job_id,
                    title=f"Parsed {job_id}",
                    recruiter="Fixture Recruiter",
                    location="London",
                    url=f"https://www.jobserve.com/job/{job_id}",
                )
                for job_id in job_ids
            ],
            [],
        ),
    )

    with scraping_client() as (client, _):
        source_id = _create_jobserve_source(client, search_url)
        response = client.post(f"/sources/{source_id}/scrape-now", json={"dry_run": True, "max_jobs": 20})

        assert response.status_code == 200
        started = response.json()
        assert started["status"] == "started"
        status = client.get(f"/scrape-runs/{started['scrape_run_id']}")
        assert status.status_code == 200
        body = status.json()
        assert body["jobs_found"] == 3
        assert body["jobs_created"] == 0
        assert [job["title"] for job in body["parsed_jobs"]] == ["Parsed D8DF", "Parsed A12345", "Parsed B67890"]


def test_jobserve_scrape_now_fetches_details_and_creates_jobs(monkeypatch) -> None:
    jobserve_html = (FIXTURES / "jobserve_search.html").read_text(encoding="utf-8")
    search_url = "https://www.jobserve.com/gb/en/JobSearch.aspx?shid=634D7F54BB4124FA4D7D"

    monkeypatch.setattr(
        source_scraping,
        "fetch_url",
        lambda url, *, delay_seconds=0: source_scraping.FetchResult(url=search_url, status_code=200, text=jobserve_html),
    )
    monkeypatch.setattr(
        source_scraping,
        "check_robots_allowed",
        lambda *args, **kwargs: RobotsCheckResult(True, "robots.txt permits this URL"),
    )
    monkeypatch.setattr(
        source_scraping,
        "_fetch_jobserve_detail_records",
        lambda job_ids, **kwargs: (
            [
                JobRecord(
                    source_job_id=job_id,
                    title=f"Data Engineer {job_id}",
                    recruiter="Fixture Recruiter",
                    location="London",
                    salary="GBP 500 per day",
                    employment_type="contract",
                    description="Build data pipelines with Python and SQL.",
                    skills=["Python", "SQL"],
                    url=f"https://www.jobserve.com/job/{job_id}",
                    apply_link=f"https://www.jobserve.com/apply/{job_id}",
                    posted_date="2026-05-18",
                )
                for job_id in job_ids
            ],
            [],
        ),
    )

    with scraping_client() as (client, TestingSession):
        source_id = _create_jobserve_source(client, search_url)
        response = client.post(f"/sources/{source_id}/scrape-now", json={"max_jobs": 2, "delay_seconds": 8})

        assert response.status_code == 200
        started = response.json()
        assert started["status"] == "started"
        status = client.get(f"/scrape-runs/{started['scrape_run_id']}")
        assert status.status_code == 200
        body = status.json()
        assert body["jobs_found"] == 2
        assert body["jobs_created"] == 2
        assert body["jobs_updated"] == 0
        assert body["parsed_jobs"][0]["title"].startswith("Data Engineer")
        with TestingSession() as db:
            jobs = db.scalars(select(Job).where(Job.source_id == source_id)).all()
            assert len(jobs) == 2
            assert {job.source_job_id for job in jobs} == {"D8DF", "A12345"}
            assert all(job.canonical_url.startswith("https://www.jobserve.com/job/") for job in jobs)


def test_jobserve_scrape_now_rejects_non_job_detail_records(monkeypatch) -> None:
    jobserve_html = (FIXTURES / "jobserve_search.html").read_text(encoding="utf-8")
    search_url = "https://www.jobserve.com/gb/en/JobSearch.aspx?shid=634D7F54BB4124FA4D7D"

    monkeypatch.setattr(
        source_scraping,
        "fetch_url",
        lambda url, *, delay_seconds=0: source_scraping.FetchResult(url=search_url, status_code=200, text=jobserve_html),
    )
    monkeypatch.setattr(
        source_scraping,
        "check_robots_allowed",
        lambda *args, **kwargs: RobotsCheckResult(True, "robots.txt permits this URL"),
    )
    monkeypatch.setattr(
        source_scraping,
        "_fetch_jobserve_detail_records",
        lambda job_ids, **kwargs: (
            [
                JobRecord(
                    source_job_id="D8DF",
                    title="Website Cookie Policy",
                    recruiter=None,
                    location=None,
                    salary=None,
                    employment_type=None,
                    description="Cookie settings and browser information.",
                    skills=[],
                    url="https://www.jobserve.com/cookie-policy",
                    apply_link=None,
                    posted_date=None,
                ),
                JobRecord(
                    source_job_id="A12345",
                    title="Data Engineer A12345",
                    recruiter="Fixture Recruiter",
                    location="London",
                    salary="GBP 500 per day",
                    employment_type="contract",
                    description="Build data pipelines with Python and SQL.",
                    skills=["Python", "SQL"],
                    url="https://www.jobserve.com/job/A12345",
                    apply_link="https://www.jobserve.com/apply/A12345",
                    posted_date="2026-05-18",
                ),
            ],
            [],
        ),
    )

    with scraping_client() as (client, TestingSession):
        source_id = _create_jobserve_source(client, search_url)
        response = client.post(f"/sources/{source_id}/scrape-now", json={"max_jobs": 2, "delay_seconds": 8})

        assert response.status_code == 200
        started = response.json()
        status = client.get(f"/scrape-runs/{started['scrape_run_id']}")
        body = status.json()
        assert body["jobs_created"] == 1
        assert body["jobs_skipped"] == 1
        with TestingSession() as db:
            jobs = db.scalars(select(Job).where(Job.source_id == source_id)).all()
            assert [job.source_job_id for job in jobs] == ["A12345"]


def test_jobserve_search_scrape_validates_request() -> None:
    with scraping_client() as (client, _):
        response = client.post(
            "/sources/jobserve/search-scrape",
            json={"keywords": "", "location": "London", "posted_within_days": 7, "remote_only": False, "max_pages": 3},
        )

        assert response.status_code == 422


def test_jobserve_search_scrape_request_accepts_full_real_form_fields() -> None:
    payload = JobServeSearchScrapeRequest(
        keywords="AI",
        location="London",
        distance="Within 50 miles",
        select_all_industries=True,
        posted_within="Within 7 days",
        job_type="Any",
        remote_only=False,
        max_pages=3,
    )

    assert payload.distance == "Within 50 miles"
    assert payload.select_all_industries is True
    assert payload.posted_within == "Within 7 days"
    assert payload.job_type == "Any"


def test_jobserve_search_form_payload_maps_full_real_form_controls() -> None:
    html = """
    <form id="frm1" action="/gb/en/JobSearch.aspx">
      <input name="ctl00$main$srch$ctl_qs$txtKey" />
      <input name="ctl00$main$srch$ctl_qs$txtLoc" />
      <select name="selRad"><option value="10">Within 10 miles</option><option value="50">Within 50 miles</option></select>
      <select name="selAge"><option value="1">Within 1 day</option><option value="7">Within 7 days</option></select>
      <select name="selJType"><option value="">Any</option><option value="P">Permanent</option></select>
      <select name="selInd" multiple><option value="it">IT</option><option value="eng">Engineering</option></select>
      <input type="checkbox" name="ctl00$main$srch$ctl_qs$RemoteWorking$chkRemoteWorking" value="on" checked />
    </form>
    """
    payload = JobServeSearchScrapeRequest(
        keywords="AI Engineer",
        location="London",
        distance="Within 50 miles",
        select_all_industries=True,
        posted_within="Within 7 days",
        job_type="Permanent",
        remote_only=False,
        max_pages=3,
    )

    data, action_url = source_scraping._jobserve_search_form_payload(html, "https://www.jobserve.com/gb/en/Job-Search/", payload)

    assert action_url == "https://www.jobserve.com/gb/en/JobSearch.aspx"
    assert data["ctl00$main$srch$ctl_qs$txtKey"] == "AI Engineer"
    assert data["ctl00$main$srch$ctl_qs$txtLoc"] == "London"
    assert data["selRad"] == "50"
    assert data["selAge"] == "7"
    assert data["selJType"] == "P"
    assert data["selInd"] == ["it", "eng"]
    assert "ctl00$main$srch$ctl_qs$RemoteWorking$chkRemoteWorking" not in data


def test_jobserve_search_diagnostics_detects_real_no_results_page(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(source_scraping, "JOBSERVE_SCRAPE_DEBUG_DIR", tmp_path)
    monkeypatch.setattr(source_scraping, "_capture_jobserve_debug_screenshot", lambda url: str(tmp_path / "results.png"))
    html = """
    <html>
      <head><title>Find AI Jobs with JobServe.com</title></head>
      <body>
        <span class="resultnumber">0</span> jobs for <strong>zzzz-no-match</strong>
        <div>No matching jobs found</div>
      </body>
    </html>
    """
    payload = JobServeSearchScrapeRequest(keywords="zzzz-no-match", location="London")

    diagnostics = source_scraping._jobserve_search_diagnostics(
        html,
        "https://www.jobserve.com/gb/en/JobSearch.aspx?shid=noresults",
        200,
        payload,
        form_data={
            "ctl00$main$srch$ctl_qs$txtKey": "zzzz-no-match",
            "ctl00$main$srch$ctl_qs$txtLoc": "London",
            "selRad": "50",
            "selAge": "7",
            "selJType": "15",
            "selInd": ["00"],
            "ctl00$main$srch$ctl_qs$btnSearch": "Search",
        },
    )

    assert diagnostics["no_results_present"] is True
    assert diagnostics["hidden_job_id_count"] == 0
    assert diagnostics["detected_result_rows"] == 0
    assert diagnostics["html_snapshot_path"]
    assert diagnostics["search_form"]["keyword_filled"] is True
    assert diagnostics["search_form"]["location_filled"] is True
    assert diagnostics["search_form"]["distance_selected"] is True
    assert diagnostics["search_form"]["posted_selected"] is True
    assert diagnostics["search_form"]["select_all_industries_applied"] is True
    assert diagnostics["search_form"]["remote_only_unchecked"] is True
    assert diagnostics["search_form"]["search_button_clicked"] is True
    assert diagnostics["search_form"]["results_loaded"] is True
    assert diagnostics["zero_result_reason"] == "jobserve_no_results"
    assert diagnostics["html_snapshot_url"].startswith("/sources/jobserve/debug-artifacts/")
    assert diagnostics["screenshot_url"].startswith("/sources/jobserve/debug-artifacts/")
    assert diagnostics["artifact_urls"]


def test_jobserve_search_diagnostics_detects_cookie_banner(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(source_scraping, "JOBSERVE_SCRAPE_DEBUG_DIR", tmp_path)
    monkeypatch.setattr(source_scraping, "_capture_jobserve_debug_screenshot", lambda url: None)
    html = """
    <html><body>
      <div>This website works best using cookies which are currently disabled.</div>
      <button>Allow essential cookies</button>
      <button>Allow all cookies</button>
    </body></html>
    """

    diagnostics = source_scraping._jobserve_search_diagnostics(html, "https://www.jobserve.com/gb/en/JobSearch.aspx?shid=cookie", 200, JobServeSearchScrapeRequest(keywords="AI"))

    assert diagnostics["cookie_banner_exists"] is True
    assert diagnostics["allow_all_cookies_exists"] is True
    assert diagnostics["allow_essential_cookies_exists"] is True


def test_jobserve_zero_extraction_with_jobs_text_is_parser_failure(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(source_scraping, "JOBSERVE_SCRAPE_DEBUG_DIR", tmp_path)
    monkeypatch.setattr(source_scraping, "_capture_jobserve_debug_screenshot", lambda url: None)
    html = "<html><body><h4 class='jobshead'>495 jobs for AI</h4><div>layout changed</div></body></html>"

    diagnostics = source_scraping._jobserve_search_diagnostics(html, "https://www.jobserve.com/gb/en/JobSearch.aspx?shid=parserfail", 200, JobServeSearchScrapeRequest(keywords="AI"))

    assert diagnostics["job_item_count"] == 0
    assert diagnostics["possible_result_list_item_count"] == 0
    assert diagnostics["zero_result_reason"] == "parser_failure_result_count_without_cards"
    assert diagnostics["html_snapshot_url"].startswith("/sources/jobserve/debug-artifacts/")


def test_jobserve_wait_for_delayed_result_markers() -> None:
    calls: list[str] = []

    class FakePage:
        def wait_for_function(self, script, timeout):
            calls.append(script)
            assert timeout == 12000

        def wait_for_timeout(self, timeout):
            raise AssertionError("fallback wait should not run")

    source_scraping._wait_for_jobserve_result_markers(FakePage())

    assert calls and ".jobItem" in calls[0]


def test_jobserve_search_diagnostics_does_not_call_results_zero_when_rows_exist(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(source_scraping, "JOBSERVE_SCRAPE_DEBUG_DIR", tmp_path)
    monkeypatch.setattr(source_scraping, "_capture_jobserve_debug_screenshot", lambda url: None)
    html = """
    <html>
      <head><title>Find AI Jobs in London with JobServe.com</title></head>
      <body>
        <span class="resultnumber">12</span> jobs for <strong>AI</strong>
        <article data-jobid="ABC123"><a class="job-title" href="/gb/en/job/ABC123">AI Engineer</a><span class="company">Acme</span></article>
      </body>
    </html>
    """
    payload = JobServeSearchScrapeRequest(keywords="AI", location="London")

    diagnostics = source_scraping._jobserve_search_diagnostics(html, "https://www.jobserve.com/gb/en/JobSearch.aspx?shid=results", 200, payload)

    assert diagnostics["no_results_present"] is False
    assert diagnostics["hidden_job_id_count"] == 0
    assert diagnostics["job_id_count"] == 1
    assert diagnostics["detected_result_rows"] == 1
    assert diagnostics["left_list_result_cards_detected"] == 1
    assert diagnostics["first_visible_results"][0]["title"] == "AI Engineer"
    assert "12 jobs for AI AI Engineer Acme" in diagnostics["visible_text_around_jobs_for"]
    assert "html_snapshot_path" not in diagnostics


def test_jobserve_search_diagnostics_reads_495_jobs_for_ai_and_selected_detail(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(source_scraping, "JOBSERVE_SCRAPE_DEBUG_DIR", tmp_path)
    monkeypatch.setattr(source_scraping, "_capture_jobserve_debug_screenshot", lambda url: str(tmp_path / "results.png"))
    html = """
    <html>
      <head><title>Find AI Jobs with JobServe.com</title></head>
      <body>
        <h4 class="cutout2 cct2 jobshead">495 jobs for AI</h4>
        <div id="B2640B418B54EFF002" class="jobItem">
          <h3 class="jobResultsTitle">AI Engineer</h3>
          <p class="jobResultsSalary">GBP 600 per day</p>
          <p class="jobResultsLoc">London</p>
          <p>Contract</p>
          <p>2 days ago</p>
        </div>
        <div id="JobDetailPanel">
          <h1>AI Engineer</h1>
          <p>Posted by: Example Recruiter Posted: Thursday, 28 May 2026</p>
          <p>Reference: B2640B418B54EFF002</p>
        </div>
      </body>
    </html>
    """

    diagnostics = source_scraping._jobserve_search_diagnostics(html, "https://www.jobserve.com/gb/en/JobSearch.aspx?shid=fixture", 200, JobServeSearchScrapeRequest(keywords="AI"))

    assert diagnostics["visible_result_count_text"] == "495 jobs for AI"
    assert diagnostics["left_list_result_cards_detected"] == 1
    assert diagnostics["first_10_visible_left_list_result_texts"][0].startswith("AI Engineer")
    assert diagnostics["selected_detail_title"] == "AI Engineer"
    assert diagnostics["selected_detail_company"] == "Example Recruiter"
    assert diagnostics["selected_detail_reference"] == "B2640B418B54EFF002"
    assert diagnostics["screenshot_path"] is None
    assert diagnostics["screenshot_capture"] == "skipped_nonzero_results"


def test_jobserve_search_diagnostics_does_not_treat_ten_jobs_as_zero(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(source_scraping, "JOBSERVE_SCRAPE_DEBUG_DIR", tmp_path)
    monkeypatch.setattr(source_scraping, "_capture_jobserve_debug_screenshot", lambda url: None)
    html = """
    <html>
      <body>
        <span class="resultnumber">10</span> jobs for <strong>AI</strong>
        <input type="hidden" id="jobIDs" value="ABC123" />
      </body>
    </html>
    """

    diagnostics = source_scraping._jobserve_search_diagnostics(html, "https://www.jobserve.com/gb/en/JobSearch.aspx?shid=results", 200, JobServeSearchScrapeRequest(keywords="AI"))

    assert diagnostics["no_results_present"] is False


def test_jobserve_search_scrape_saves_visible_left_list_jobs_when_hidden_ids_missing(monkeypatch) -> None:
    jobserve_html = """
    <html>
      <head><title>Find AI Jobs in London with JobServe.com</title></head>
      <body>
        <h4 class="jobshead">495 jobs for AI</h4>
        <div id="B2640B418B54EFF002" class="jobItem">
          <h3 class="jobResultsTitle">AI & Power Platform Solutions Engineer</h3>
          <p class="jobResultsSalary">GBP 80k - GBP 95k - depending on experience</p>
          <p class="jobResultsLoc">London</p>
          <p>Permanent</p>
          <p>6 hours ago</p>
        </div>
        <div id="C409B81B016FCBB60F" class="jobItem">
          <h3 class="jobResultsTitle">AI Architect</h3>
          <p class="jobResultsLoc">London</p>
          <p>Contract</p>
          <p>1 day ago</p>
        </div>
      </body>
    </html>
    """
    visible = source_scraping.extract_jobserve_visible_results(jobserve_html)
    diagnostics = source_scraping._jobserve_search_diagnostics(jobserve_html, "https://www.jobserve.com/gb/en/JobSearch.aspx?shid=fixture", 200, JobServeSearchScrapeRequest(keywords="AI"))

    monkeypatch.setattr(
        source_scraping,
        "_fetch_jobserve_search_results",
        lambda payload: source_scraping.JobServeSearchResultPage(
            url="https://www.jobserve.com/gb/en/JobSearch.aspx?shid=fixture",
            html=jobserve_html,
            job_ids=[],
            status_code=200,
            cookies={},
            diagnostics=diagnostics,
            visible_results=visible,
        ),
    )
    monkeypatch.setattr(
        source_scraping,
        "_fetch_jobserve_detail_records",
        lambda job_ids, **kwargs: ([], [f"JobServe {job_id}: detail endpoint unavailable" for job_id in job_ids]),
    )

    with scraping_client() as (client, TestingSession):
        response = client.post("/sources/jobserve/search-scrape", json={"keywords": "AI", "location": "London", "max_pages": 3})
        status = client.get(f"/sources/scrape-runs/{response.json()['run_id']}")

        assert status.status_code == 200
        body = status.json()
        assert body["found"] == 2
        assert body["created"] == 2
        assert body["skipped"] == 0
        assert body["error"] is None
        with TestingSession() as db:
            jobs = db.scalars(select(Job)).all()
            assert {job.source_job_id for job in jobs} == {"B2640B418B54EFF002", "C409B81B016FCBB60F"}
            assert {job.original_external_id for job in jobs} == {"B2640B418B54EFF002", "C409B81B016FCBB60F"}
            assert all(job.canonical_url.startswith("https://www.jobserve.com/gb/en/job/") for job in jobs)
            assert all("JobSearch.aspx" not in job.canonical_url for job in jobs)


def test_jobserve_search_scrape_persists_diagnostics_in_run_status(monkeypatch) -> None:
    jobserve_html = """
    <html>
      <head><title>Find AI Jobs in London with JobServe.com</title></head>
      <body>
        <span class="resultnumber">1</span> jobs for <strong>AI</strong>
        <input type="hidden" id="jobIDs" value="ABC123" />
      </body>
    </html>
    """

    monkeypatch.setattr(source_scraping, "_capture_jobserve_debug_screenshot", lambda url: "backend/debug_artifacts/jobserve_scrape/results.png")
    monkeypatch.setattr(
        source_scraping,
        "_fetch_jobserve_search_results",
        lambda payload: source_scraping.JobServeSearchResultPage(
            url="https://www.jobserve.com/gb/en/JobSearch.aspx?shid=fixture",
            html=jobserve_html,
            job_ids=["ABC123"],
            status_code=200,
            cookies={},
            diagnostics=source_scraping._jobserve_search_diagnostics(jobserve_html, "https://www.jobserve.com/gb/en/JobSearch.aspx?shid=fixture", 200, payload),
        ),
    )
    monkeypatch.setattr(
        source_scraping,
        "_fetch_jobserve_detail_records",
        lambda job_ids, **kwargs: (
            [
                JobRecord(
                    source_job_id="ABC123",
                    title="AI Engineer",
                    recruiter="Search Recruiter",
                    location="London",
                    description="Build AI services.",
                    url="https://www.jobserve.com/gb/en/job/ABC123",
                )
            ],
            [],
        ),
    )

    with scraping_client() as (client, _):
        response = client.post("/sources/jobserve/search-scrape", json={"keywords": "AI", "location": "London", "max_pages": 3})
        status = client.get(f"/sources/scrape-runs/{response.json()['run_id']}")

        assert status.status_code == 200
        body = status.json()
        assert body["found"] == 1
        assert body["diagnostics"]["final_url"] == "https://www.jobserve.com/gb/en/JobSearch.aspx?shid=fixture"
        assert body["diagnostics"]["page_title"] == "Find AI Jobs in London with JobServe.com"
        assert body["diagnostics"]["hidden_job_id_count"] == 1


def test_jobserve_search_scrape_summary_dedupe_and_fingerprints(monkeypatch) -> None:
    jobserve_html = (FIXTURES / "jobserve_search.html").read_text(encoding="utf-8")

    monkeypatch.setattr(
        source_scraping,
        "_fetch_jobserve_search_results",
        lambda payload: source_scraping.JobServeSearchResultPage(
            url="https://www.jobserve.com/gb/en/JobSearch.aspx?shid=fixture",
            html=jobserve_html,
            job_ids=["D8DF", "A12345", "B67890"],
            status_code=200,
            cookies={"JSFX": "fixture"},
        ),
    )
    monkeypatch.setattr(
        source_scraping,
        "_fetch_jobserve_detail_records",
        lambda job_ids, **kwargs: (
            [
                JobRecord(
                    source_job_id=job_id,
                    title=f"AI Engineer {job_id}",
                    recruiter="Search Recruiter",
                    location="London",
                    salary="GBP 600 per day",
                    employment_type="contract",
                    description="Build AI automation services with Python.",
                    skills=["Python", "AI"],
                    url=f"https://www.jobserve.com/job/{job_id}",
                    apply_link=f"https://www.jobserve.com/apply/{job_id}",
                    posted_date="2026-05-24",
                )
                for job_id in job_ids
            ],
            [],
        ),
    )

    with scraping_client() as (client, TestingSession):
        first = client.post(
            "/sources/jobserve/search-scrape",
            json={"keywords": "AI", "location": "London", "posted_within_days": 7, "remote_only": False, "max_pages": 3},
        )
        second = client.post(
            "/sources/jobserve/search-scrape",
            json={"keywords": "AI", "location": "London", "posted_within_days": 7, "remote_only": False, "max_pages": 3},
        )

        assert first.status_code == 200
        assert second.status_code == 200
        first_status = client.get(f"/sources/scrape-runs/{first.json()['run_id']}")
        second_status = client.get(f"/sources/scrape-runs/{second.json()['run_id']}")
        assert first.json()["status"] == "running"
        assert first_status.json()["found"] == 3
        assert first_status.json()["search_params"]["distance"] == "Within 50 miles"
        assert first_status.json()["search_params"]["posted_within"] == "Within 7 days"
        assert first_status.json()["search_params"]["job_type"] == "Any"
        assert first_status.json()["search_params"]["select_all_industries"] is True
        assert first_status.json()["final_search_url"] == "https://www.jobserve.com/gb/en/JobSearch.aspx?shid=fixture"
        assert first_status.json()["result_count"] == 3
        assert first_status.json()["created"] == 3
        assert first_status.json()["updated"] == 0
        assert first_status.json()["skipped"] == 0
        assert second_status.json()["created"] == 0
        assert second_status.json()["updated"] == 3
        with TestingSession() as db:
            jobs = db.scalars(select(Job)).all()
            assert len(jobs) == 3
            assert all(job.original_title == job.title for job in jobs)
            assert all(job.original_company == "Search Recruiter" for job in jobs)
            assert all(job.original_location == "London" for job in jobs)
            assert all(job.original_salary == "GBP 600 per day" for job in jobs)
            assert all(job.original_external_id in {"D8DF", "A12345", "B67890"} for job in jobs)
            assert all("/job/" in job.canonical_url for job in jobs)
            assert all("/apply/" not in job.canonical_url for job in jobs)
            assert all(job.content_hash for job in jobs)


def test_scrape_now_creates_snapshot_job_analysis_and_score(monkeypatch) -> None:
    directory_html = (FIXTURES / "generic_directory.html").read_text(encoding="utf-8")
    job_html = (FIXTURES / "generic_job_jsonld.html").read_text(encoding="utf-8")

    def fake_fetch(url: str, *, delay_seconds: float = 0):
        if "senior-data-engineer" in url or "data-engineer" in url:
            return source_scraping.FetchResult(url="https://example.invalid/jobs/senior-data-engineer", status_code=200, text=job_html)
        return source_scraping.FetchResult(url=url, status_code=200, text=directory_html)

    monkeypatch.setattr(source_scraping, "fetch_url", fake_fetch)
    monkeypatch.setattr(
        source_scraping,
        "check_robots_allowed",
        lambda *args, **kwargs: RobotsCheckResult(True, "robots.txt permits this URL"),
    )

    with scraping_client() as (client, TestingSession):
        source_id = _create_source(client)
        response = client.post(f"/sources/{source_id}/scrape-now", json={"max_jobs": 1, "max_pages": 1, "delay_seconds": 8})

        assert response.status_code == 200
        started = response.json()
        assert started["status"] == "started"
        status = client.get(f"/scrape-runs/{started['scrape_run_id']}")
        assert status.status_code == 200
        body = status.json()
        assert body["jobs_found"] == 1
        assert body["jobs_created"] == 1
        with TestingSession() as db:
            job = db.scalar(select(Job))
            assert job is not None
            assert db.scalar(select(RawJobSnapshot).where(RawJobSnapshot.source_id == source_id)) is not None
            assert db.scalar(select(JobAnalysis).where(JobAnalysis.job_id == job.id)) is not None
            assert db.scalar(select(JobScore).where(JobScore.job_id == job.id)) is not None


def _create_source(client: TestClient) -> int:
    response = client.post(
        "/sources/from-url",
        json={
            "name": "Fixture Careers",
            "base_url": "https://example.invalid/careers",
            "source_type": "careers",
            "permission_notes": "Reviewed and permitted.",
            "scraping_allowed": True,
            "rate_limit_per_minute": 10,
            "allowed_path_patterns": ["/jobs/", "/careers/"],
            "job_link_patterns": ["/jobs/", "/careers/"],
        },
    )
    return int(response.json()["id"])


def _create_jobserve_source(client: TestClient, search_url: str) -> int:
    response = client.post(
        "/sources/from-url",
        json={
            "name": "JobServe",
            "base_url": search_url,
            "source_type": "careers",
            "permission_notes": "Reviewed and permitted.",
            "scraping_allowed": True,
            "rate_limit_per_minute": 10,
            "allowed_path_patterns": None,
            "job_link_patterns": None,
        },
    )
    return int(response.json()["id"])
