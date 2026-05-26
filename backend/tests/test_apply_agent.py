from collections.abc import Generator
from contextlib import contextmanager
from types import SimpleNamespace
from pathlib import Path
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Job, JobScore, JobSource, User, UserProfile
from app.db.session import get_db
from app.main import app
from app.schemas.database import AssistApplyResult
from app.api import applications as applications_api
from app.services import apply_agent


def test_blocked_strategy_is_rejected(db_session) -> None:
    user, job = _seed_application(db_session)
    job.apply_strategy = "blocked"
    job.apply_difficulty = "blocked"
    db_session.commit()

    try:
        apply_agent.assist_apply_application(db_session, job, user, browser_runner=_fake_runner)
    except ValueError as exc:
        assert "Blocked apply routes" in str(exc)
    else:
        raise AssertionError("blocked strategy should be rejected")


def test_unavailable_job_is_rejected(db_session, monkeypatch) -> None:
    user, job = _seed_application(db_session)
    monkeypatch.setattr(
        apply_agent,
        "check_job_availability",
        lambda db, candidate: SimpleNamespace(availability_status="unavailable", availability_reason="HTTP 404"),
    )

    try:
        apply_agent.assist_apply_application(db_session, job, user, browser_runner=_fake_runner)
    except ValueError as exc:
        assert "job is unavailable" in str(exc)
    else:
        raise AssertionError("unavailable job should be rejected")


def test_missing_apply_url_is_rejected(db_session) -> None:
    user, job = _seed_application(db_session, url="")

    try:
        apply_agent.assist_apply_application(db_session, job, user, browser_runner=_fake_runner)
    except ValueError as exc:
        assert "Missing apply URL" in str(exc)
    else:
        raise AssertionError("missing apply URL should be rejected")


def test_safe_field_mapping_uses_exact_profile_values(db_session) -> None:
    user, _ = _seed_application(db_session)
    profile = UserProfile(
        user_id=user.id,
        cv_text="CV",
        preferences={
            "full_name": "Alex Applicant",
            "phone": "+44 7000 000000",
            "linkedin": "https://linkedin.com/in/alex",
            "work_authorization": "UK citizen, no sponsorship required",
        },
        location_preference="London",
    )
    db_session.add(profile)
    db_session.commit()

    candidates = apply_agent.profile_field_candidates(user, profile)

    assert candidates["email"].value == "apply-agent@example.invalid"
    assert candidates["name"].value == "Alex Applicant"
    assert apply_agent.classify_form_field("Email address") == "email"
    assert apply_agent.classify_form_field("Visa sponsorship required?") == "work_authorization"
    assert apply_agent.classify_form_field("First name") == "first_name"


def test_assist_apply_endpoint_never_submits(monkeypatch) -> None:
    submitted = False

    def fake_runner(url, candidates, profile, mode, apply_strategy, **kwargs):
        nonlocal submitted
        assert mode == "review_only"
        submitted = False
        return AssistApplyResult(
            status="review_required",
            filled_fields=["Email"],
            unfilled_fields=["First name"],
            unfilled_required_fields=[],
            uploaded_cv=False,
            submitted=False,
            warnings=["Submit control detected and intentionally not clicked."],
            screenshot_path=None,
        )

    monkeypatch.setattr(
        apply_agent,
        "check_job_availability",
        lambda db, candidate: SimpleNamespace(availability_status="active", availability_reason="fixture"),
    )
    monkeypatch.setattr(apply_agent, "run_playwright_assist", fake_runner)
    with apply_client() as (client, ids):
        response = client.post(f"/applications/{ids['job']}/assist-apply")

    assert response.status_code == 200
    assert response.json()["status"] == "review_required"
    assert submitted is False
    assert any("intentionally not clicked" in warning for warning in response.json()["warnings"])


def test_assist_apply_endpoint_queues_when_queue_enabled(monkeypatch) -> None:
    enqueued = []
    monkeypatch.setattr(applications_api, "queue_enabled", lambda: True)
    monkeypatch.setattr(
        applications_api,
        "enqueue_or_background",
        lambda background_tasks, func, *args, **kwargs: enqueued.append((func, args, kwargs)) or "rq-job",
    )

    with apply_client() as (client, ids):
        response = client.post(f"/applications/{ids['job']}/assist-apply")

    assert response.status_code == 200
    assert response.json()["status"] == "queued"
    assert enqueued
    assert enqueued[0][0] is apply_agent.run_assist_apply_background
    assert enqueued[0][1][0] == ids["job"]


def test_assist_apply_endpoint_queues_debug_mode(monkeypatch) -> None:
    enqueued = []
    monkeypatch.setattr(applications_api, "queue_enabled", lambda: True)
    monkeypatch.setattr(
        applications_api,
        "enqueue_or_background",
        lambda background_tasks, func, *args, **kwargs: enqueued.append((func, args, kwargs)) or "rq-job",
    )

    with apply_client(jobserve=True, with_profile=True) as (client, ids):
        response = client.post(f"/applications/{ids['job']}/assist-apply", json={"mode": "review_only", "debug_mode": True})

    assert response.status_code == 200
    assert response.json()["status"] == "queued"
    assert enqueued[0][0] is apply_agent.run_assist_apply_background
    assert enqueued[0][1] == (ids["job"], ids["user"], "review_only", True)


def test_submit_requires_explicit_mode(monkeypatch) -> None:
    seen_modes = []

    def fake_runner(url, candidates, profile=None, mode="review_only", apply_strategy="unknown", **kwargs):
        seen_modes.append(mode)
        return AssistApplyResult(status="review_required", filled_fields=[], unfilled_fields=[], warnings=[], screenshot_path=None)

    monkeypatch.setattr(apply_agent, "check_job_availability", lambda db, candidate: SimpleNamespace(availability_status="active", availability_reason="fixture"))
    monkeypatch.setattr(apply_agent, "run_playwright_assist", fake_runner)
    with apply_client(jobserve=True, with_profile=True) as (client, ids):
        response = client.post(f"/applications/{ids['job']}/assist-apply", json={"mode": "review_only"})

    assert response.status_code == 200
    assert seen_modes == ["review_only"]


def test_missing_cv_blocks_submit(monkeypatch) -> None:
    monkeypatch.setattr(apply_agent, "check_job_availability", lambda db, candidate: SimpleNamespace(availability_status="active", availability_reason="fixture"))
    with apply_client(jobserve=True, with_profile=True, with_cv=False) as (client, ids):
        response = client.post(f"/applications/{ids['job']}/assist-apply", json={"mode": "submit_with_confirmation"})

    assert response.status_code == 400
    assert "Saved CV file is required" in response.json()["detail"]


def test_missing_email_blocks_submit(monkeypatch) -> None:
    monkeypatch.setattr(apply_agent, "check_job_availability", lambda db, candidate: SimpleNamespace(availability_status="active", availability_reason="fixture"))
    with apply_client(jobserve=True, with_profile=True, with_cv=True, email="") as (client, ids):
        response = client.post(f"/applications/{ids['job']}/assist-apply", json={"mode": "submit_with_confirmation"})

    assert response.status_code == 400
    assert "Email is required" in response.json()["detail"]


def test_unavailable_job_blocks_submit(monkeypatch) -> None:
    monkeypatch.setattr(apply_agent, "check_job_availability", lambda db, candidate: SimpleNamespace(availability_status="expired", availability_reason="closed"))
    with apply_client(jobserve=True, with_profile=True, with_cv=True) as (client, ids):
        response = client.post(f"/applications/{ids['job']}/assist-apply", json={"mode": "submit_with_confirmation"})

    assert response.status_code == 400
    assert "job is expired" in response.json()["detail"]


def test_successful_jobserve_submit_marks_applied(monkeypatch) -> None:
    def fake_runner(url, candidates, profile=None, mode="review_only", apply_strategy="unknown", **kwargs):
        assert mode == "submit_with_confirmation"
        assert apply_strategy == "jobserve_apply_easy"
        return AssistApplyResult(
            status="submitted",
            filled_fields=["Email Address", "CV upload"],
            unfilled_fields=[],
            unfilled_required_fields=[],
            uploaded_cv=True,
            submitted=True,
            warnings=["Disabled option: register a Job Seeker account."],
            screenshot_path=None,
        )

    monkeypatch.setattr(apply_agent, "check_job_availability", lambda db, candidate: SimpleNamespace(availability_status="active", availability_reason="fixture"))
    monkeypatch.setattr(apply_agent, "run_playwright_assist", fake_runner)
    with apply_client(jobserve=True, with_profile=True, with_cv=True) as (client, ids):
        response = client.post(f"/applications/{ids['job']}/assist-apply", json={"mode": "submit_with_confirmation"})
        with ids["Session"]() as db:
            job = db.get(Job, ids["job"])

    assert response.status_code == 200
    assert response.json()["submitted"] is True
    assert job.application_status == "applied"
    assert job.applied_at is not None


def test_account_registration_toggles_are_disabled_if_present() -> None:
    warnings = []
    controls = [_FakeControl(True), _FakeControl(False)]
    apply_agent._disable_jobserve_account_options(_FakePage(controls), warnings)

    assert controls[0].unchecked is True
    assert controls[1].unchecked is False
    assert any("Disabled option" in warning for warning in warnings)


def test_availability_dropdown_selects_immediate() -> None:
    page = _FakeSelectPage()

    diagnostics: list[dict] = []

    assert apply_agent._select_dropdown_by_label_patterns(page, [r"availability"], "Immediate", diagnostics=diagnostics) is True
    assert page.selected == "Immediate"
    assert diagnostics[0]["available_options"]
    assert diagnostics[0]["strategy"] == "exact_label"


def test_select_dropdown_falls_back_to_normalized_text() -> None:
    page = _FakeSelectPage(options=["Please select", "1 Month"])
    diagnostics: list[dict] = []

    assert apply_agent._select_dropdown_by_label_patterns(page, [r"availability"], "1 month", diagnostics=diagnostics) is True

    assert page.selected == "1 Month"
    assert diagnostics[0]["strategy"] == "normalized_label"


def test_select_dropdown_falls_back_to_option_index() -> None:
    page = _FakeSelectPage(options=["Please select", "Immediate", "1 Month"])
    diagnostics: list[dict] = []

    assert apply_agent._select_dropdown_by_label_patterns(page, [r"availability"], "2", diagnostics=diagnostics) is True

    assert page.selected == "1 Month"
    assert diagnostics[0]["strategy"] == "fallback_option_index"


def test_cv_upload_path_materializes_database_blob(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(apply_agent, "WORKER_CV_DIR", tmp_path)
    profile = SimpleNamespace(cv_file_path=str(tmp_path / "missing.pdf"), cv_file_name="my cv.pdf", cv_file_bytes=b"%PDF-1.4", cv_file_mime_type="application/pdf", cv_file_size=8)
    diagnostics: dict = {}

    path = apply_agent._cv_upload_path(profile, diagnostics)

    assert path is not None
    assert Path(path).exists()
    assert diagnostics["materialized_from_blob"] is True
    assert diagnostics["path_exists"] is True
    assert diagnostics["path_file_size"] == 8


def test_jobserve_search_defaults_are_configured() -> None:
    prefs = apply_agent._jobserve_search_preferences(SimpleNamespace(preferences={}))

    assert prefs["keywords"] == "AI"
    assert prefs["location"] == "London"
    assert prefs["distance"] == "Within 50 miles"
    assert prefs["posted_within"] == "Within 7 days"
    assert prefs["job_type"] == "Any"
    assert prefs["working_status"] == "UK Citizen"


def test_jobserve_results_target_matching_prefers_reference_title_and_company() -> None:
    candidates = [
        {"text": "Other AI Engineer Example", "href": "/1", "title": "Other", "company": "Example", "reference": "X"},
        {"text": "Senior AI Engineer Acme Ref D8DF", "href": "/2", "title": "Senior AI Engineer", "company": "Acme", "reference": "D8DF"},
    ]
    target = {"title": "Senior AI Engineer", "company_name": "Acme", "source_job_id": "D8DF"}

    ranked = apply_agent._rank_jobserve_candidates(candidates, target)

    assert ranked[0]["href"] == "/2"
    assert ranked[0]["score"] > ranked[1]["score"]


def test_submit_validation_allows_optional_salary_travel_defaults(db_session) -> None:
    user, job = _seed_application(db_session, jobserve=True)
    profile = UserProfile(user_id=user.id, cv_text="CV", email="apply-agent@example.invalid", cv_file_bytes=b"cv", cv_file_name="cv.pdf")
    db_session.add(profile)
    db_session.add(JobScore(job_id=job.id, user_id=user.id, total_score=90, recommendation="apply", recommendation_tier="Strong match"))
    db_session.commit()

    apply_agent._validate_jobserve_submit(db_session, job, user, profile)


def test_assist_progress_heartbeat_persists(db_session) -> None:
    user, job = _seed_application(db_session, jobserve=True)

    apply_agent._persist_assist_progress(db_session, job, "search_page_loaded", {"fixture": True}, time.perf_counter())

    db_session.refresh(job)
    assert job.assisted_result["status"] == "running"
    assert job.assisted_result["progress"]["current_step"] == "search_page_loaded"
    assert job.assisted_result["progress"]["last_heartbeat_at"]
    assert job.assisted_result["timing_diagnostics"]["total_runtime_ms"] >= 0


def test_playwright_result_includes_timing_from_fake_browser_runner(db_session, monkeypatch) -> None:
    user, job = _seed_application(db_session)
    monkeypatch.setattr(apply_agent, "check_job_availability", lambda db, candidate: SimpleNamespace(availability_status="active", availability_reason="fixture"))

    def runner(url, candidates, profile, mode, apply_strategy):
        return AssistApplyResult(status="review_required", timing_diagnostics={"total_runtime_ms": 123}, progress={"current_step": "review_required"})

    result = apply_agent.assist_apply_application(db_session, job, user, browser_runner=runner)

    assert result.timing_diagnostics["total_runtime_ms"] == 123


def test_salary_65000_selects_50_to_75_range() -> None:
    assert apply_agent.salary_range_label("65000") == "£50,000 - £75,000"


def test_salary_90000_selects_75_to_100_range() -> None:
    assert apply_agent.salary_range_label("90000") == "£75,000 - £100,000"


def test_travel_25_selects_16_to_30() -> None:
    assert apply_agent.travel_distance_label("25") == "16 to 30"


def test_missing_optional_dropdowns_do_not_block_submit_validation(monkeypatch) -> None:
    monkeypatch.setattr(apply_agent, "check_job_availability", lambda db, candidate: SimpleNamespace(availability_status="active", availability_reason="fixture"))
    with apply_client(jobserve=True, with_profile=True, with_cv=True, dropdowns=False) as (_client, ids):
        with ids["Session"]() as db:
            job = db.get(Job, ids["job"])
            user = db.get(User, ids["user"])
            profile = db.query(UserProfile).filter(UserProfile.user_id == ids["user"]).one()
            apply_agent._validate_jobserve_submit(db, job, user, profile)


def test_review_only_leaves_missing_dropdown_blank() -> None:
    warnings: list[str] = []
    filled: list[str] = []
    unfilled_required: list[str] = []
    apply_agent._handle_required_dropdown(
        _FakeSelectPage(),
        {},
        "availability_notice",
        [r"availability"],
        lambda value: value,
        "Availability notice",
        "Availability notice missing",
        filled,
        unfilled_required,
        warnings,
    )

    assert filled == []
    assert "Availability notice" in unfilled_required
    assert "Availability notice missing" in warnings


def test_default_threshold_is_80(db_session) -> None:
    user = User(email="threshold@example.invalid")
    db_session.add(user)
    db_session.commit()

    from app.services.applications import minimum_apply_score

    assert minimum_apply_score(db_session, user) == 80


def test_submit_apply_blocks_below_threshold(monkeypatch) -> None:
    monkeypatch.setattr(apply_agent, "check_job_availability", lambda db, candidate: SimpleNamespace(availability_status="active", availability_reason="fixture"))
    with apply_client(jobserve=True, with_profile=True, with_cv=True, score=74, minimum_apply_score=80) as (client, ids):
        response = client.post(f"/applications/{ids['job']}/assist-apply", json={"mode": "submit_with_confirmation"})

    assert response.status_code == 400
    assert "Job score 74 is below your apply threshold of 80." in response.json()["detail"]


def test_debug_mode_returns_debug_artifact_fields(monkeypatch) -> None:
    def fake_runner(url, candidates, profile, mode, apply_strategy, **kwargs):
        assert kwargs["debug_mode"] is True
        return AssistApplyResult(
            status="review_required",
            filled_fields=[],
            unfilled_fields=[],
            warnings=[],
            screenshot_path=None,
            debug_mode=True,
            screenshot_paths=["backend/runtime/apply_debug/1/initial.png"],
            html_snapshot_paths=["backend/runtime/apply_debug/1/no_modal.html"],
            detected_buttons=[{"text": "Apply"}],
            detected_fields=[{"label": "Email", "name": "email"}],
            detected_selects=[{"label": "Availability notice"}],
            detected_iframes=[{"src": "about:blank"}],
            final_url="https://example.invalid/apply",
            final_error="fixture",
        )

    monkeypatch.setattr(apply_agent, "check_job_availability", lambda db, candidate: SimpleNamespace(availability_status="active", availability_reason="fixture"))
    monkeypatch.setattr(apply_agent, "run_playwright_assist", fake_runner)
    with apply_client(jobserve=True, with_profile=True) as (client, ids):
        response = client.post(f"/applications/{ids['job']}/assist-apply", json={"mode": "review_only", "debug_mode": True})

    assert response.status_code == 200
    body = response.json()
    assert body["debug_mode"] is True
    assert body["screenshot_paths"]
    assert body["html_snapshot_paths"]
    assert body["detected_buttons"][0]["text"] == "Apply"
    assert body["final_error"] == "fixture"


def test_assist_apply_debug_payload_survives_worker_db_api(monkeypatch) -> None:
    def fake_runner(url, candidates, *, profile=None, mode="review_only", apply_strategy="unknown", debug_mode=False, **kwargs):
        assert debug_mode is True
        return AssistApplyResult(
            status="review_required",
            filled_fields=[],
            unfilled_fields=[],
            warnings=[],
            screenshot_path=None,
            debug_mode=True,
            screenshot_paths=["backend/runtime/apply_debug/123/01_initial.png"],
            screenshot_urls=["/applications/debug-artifacts/123/01_initial.png"],
            html_snapshot_paths=["backend/runtime/apply_debug/123/01_modal.html"],
            html_snapshot_urls=["/applications/debug-artifacts/123/01_modal.html"],
            detected_buttons=[{"text": "Apply", "selector": "button"}],
            detected_fields=[{"label": "Email", "name": "email"}],
            detected_selects=[{"label": "Availability", "options": ["Immediate"]}],
            detected_iframes=[{"src": "about:blank"}],
            debug_steps=[{"step": "initial_page_loaded", "iframe_count": 1, "popup_window_count": 1}],
            final_url="https://www.jobserve.com/apply",
            final_error="fixture selector miss",
        )

    monkeypatch.setattr(apply_agent, "check_job_availability", lambda db, candidate: SimpleNamespace(availability_status="active", availability_reason="fixture"))
    monkeypatch.setattr(apply_agent, "run_playwright_assist", fake_runner)
    with apply_client(jobserve=True, with_profile=True) as (client, ids):
        monkeypatch.setattr(apply_agent, "SessionLocal", ids["Session"])
        apply_agent.run_assist_apply_background(ids["job"], ids["user"], "review_only", True)
        response = client.get("/applications")

    assert response.status_code == 200
    item = next(candidate for candidate in response.json()["items"] if candidate["job_id"] == ids["job"])
    persisted = item["assisted_result"]
    assert persisted["debug_mode"] is True
    assert persisted["debug_steps"][0]["step"] == "initial_page_loaded"
    assert persisted["screenshot_urls"] == ["/applications/debug-artifacts/123/01_initial.png"]
    assert persisted["html_snapshot_urls"] == ["/applications/debug-artifacts/123/01_modal.html"]
    assert persisted["detected_fields"][0]["name"] == "email"
    assert persisted["final_url"] == "https://www.jobserve.com/apply"
    assert persisted["final_error"] == "fixture selector miss"


def test_debug_artifact_route_serves_runtime_file(tmp_path, monkeypatch) -> None:
    artifact_dir = tmp_path / "123"
    artifact_dir.mkdir()
    artifact = artifact_dir / "01_modal.html"
    artifact.write_text("<html>debug</html>", encoding="utf-8")
    monkeypatch.setattr(applications_api, "DEBUG_ARTIFACT_ROOT", tmp_path.resolve())

    with apply_client() as (client, _ids):
        response = client.get("/applications/debug-artifacts/123/01_modal.html")

    assert response.status_code == 200
    assert "debug" in response.text


def test_jobserve_no_modal_found_returns_clear_reason_and_html_snapshot(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(apply_agent, "DEBUG_ARTIFACT_DIR", tmp_path)
    with _playwright_page() as (page, browser):
        page.set_content("<html><title>No Modal</title><body><button>Apply</button><main>No application here</main></body></html>")

        result = apply_agent._run_jobserve_modal(page, browser, {}, None, mode="review_only", keep_open_for_review=False, debug_mode=True)

    assert result.status == "review_required"
    assert result.final_error == "Job Application modal/form not found after clicking Apply."
    assert result.html_snapshot_paths
    assert Path(result.html_snapshot_paths[0]).exists()


def test_jobserve_iframe_form_detection(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(apply_agent, "DEBUG_ARTIFACT_DIR", tmp_path)
    iframe = """
    <iframe srcdoc="<h1>Job Application</h1><label>Email <input name='email' /></label><input type='file' name='cv' /><select name='availability'><option>Immediate</option></select>"></iframe>
    """
    with _playwright_page() as (page, browser):
        page.set_content(f"<button>Apply</button>{iframe}")
        page.wait_for_timeout(500)

        result = apply_agent._run_jobserve_modal(page, browser, {}, None, mode="review_only", keep_open_for_review=False, debug_mode=True)

    assert result.final_error is None
    assert any(field.get("name") == "email" for field in result.detected_fields)
    assert any(select.get("name") == "availability" for select in result.detected_selects)


def test_visible_field_inventory_ignores_hidden_and_reports_select_options() -> None:
    with _playwright_page() as (page, _browser):
        page.set_content(
            """
            <label>Email <input name="email" value="alex@example.invalid" /></label>
            <input type="hidden" name="csrf" value="secret" />
            <label>Availability <select name="availability"><option>Immediate</option><option>One month</option></select></label>
            <button>Apply</button>
            """
        )

        inventory = apply_agent._inventory_context(page)

    assert [field["name"] for field in inventory["fields"]] == ["email", "availability"]
    assert inventory["selects"][0]["options"] == ["Immediate", "One month"]
    assert inventory["buttons"][0]["text"] == "Apply"


def test_jobserve_review_and_debug_mode_do_not_submit(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(apply_agent, "DEBUG_ARTIFACT_DIR", tmp_path)
    html = """
    <button>Apply</button>
    <div role="dialog">
      <h1>Job Application</h1>
      <label>Email <input name="email" /></label>
      <button type="submit" onclick="window.submitted = (window.submitted || 0) + 1">Apply</button>
    </div>
    """
    with _playwright_page() as (page, browser):
        page.set_content(html)
        page.evaluate("window.submitted = 0")

        result = apply_agent._run_jobserve_modal(
            page,
            browser,
            {"email": apply_agent.FieldCandidate(key="email", value="alex@example.invalid", reason="test")},
            None,
            mode="review_only",
            keep_open_for_review=False,
            debug_mode=True,
        )
        submitted = page.evaluate("window.submitted")

    assert result.submitted is False
    assert submitted == 0
    assert any("Debug mode" in warning for warning in result.warnings)


def test_jobserve_search_form_fill_select_all_industries() -> None:
    with _playwright_page() as (page, _browser):
        page.set_content(
            """
            <label>Keywords <input name="keywords" /></label>
            <label>Location <input name="location" /></label>
            <label>Distance <select name="distance"><option>Within 50 miles</option></select></label>
            <label>Posted <select name="posted"><option>Within 7 days</option></select></label>
            <label>Job Type <select name="type"><option>Any</option></select></label>
            <label>Remote only <input type="checkbox" checked /></label>
            <button type="button">Industries</button><button type="button" onclick="window.selectedAll = true">Select All</button>
            """
        )
        flow = {"search_defaults": apply_agent._jobserve_search_preferences(SimpleNamespace(preferences={})), "search_controls": {}}

        assert apply_agent._fill_jobserve_search_form(page, flow, []) is True

        assert page.locator("input[name=keywords]").input_value() == "AI"
        assert page.locator("input[name=location]").input_value() == "London"
        assert page.get_by_label("Remote only").is_checked() is False
        assert page.evaluate("window.selectedAll") is True


def test_jobserve_modal_fill_uploads_filcv_and_review_only_does_not_submit(tmp_path, monkeypatch) -> None:
    cv_path = tmp_path / "cv.pdf"
    cv_path.write_bytes(b"%PDF")
    profile = SimpleNamespace(
        cv_file_path=str(cv_path),
        cv_file_name="cv.pdf",
        cv_file_bytes=None,
        cv_file_mime_type="application/pdf",
        cv_file_size=4,
        preferences={},
    )
    with _playwright_page() as (page, _browser):
        page.set_content(
            """
            <form>
              <label>Email Address <input name="email" /></label>
              <label>Send confirmation of my application to this Email Address <input type="checkbox" /></label>
              <label>Working status in UK <select name="status"><option></option><option>UK Citizen</option></select></label>
              <input id="filCV" type="file" />
              <button type="button" onclick="window.submitted = true">Apply</button>
            </form>
            """
        )
        flow = {}
        filled: list[str] = []
        unfilled: list[str] = []
        required: list[str] = []
        debug = apply_agent._ApplyDebugRecorder(page, _browser, enabled=False)

        result = apply_agent._fill_jobserve_application_form(
            page,
            page,
            {
                "email": apply_agent.FieldCandidate("email", "alex@example.invalid", "test"),
                "work_authorization": apply_agent.FieldCandidate("work_authorization", "UK Citizen", "test"),
            },
            profile,
            mode="review_only",
            flow=flow,
            filled=filled,
            unfilled=unfilled,
            unfilled_required=required,
            warnings=[],
            upload_diagnostics={},
            select_diagnostics=[],
            profile_diagnostics={"mapped_fields": {}},
            exceptions=[],
            debug=debug,
        )

        assert result["uploaded_cv"] is True
        assert page.locator("input[name=email]").input_value() == "alex@example.invalid"
        assert page.get_by_label("Send confirmation of my application to this Email Address").is_checked() is True
        assert page.evaluate("window.submitted") is None
        assert "CV upload" in filled


def test_confirmation_detection_and_account_toggle_off() -> None:
    warnings: list[str] = []
    with _playwright_page() as (page, _browser):
        page.set_content(
            """
            <div>Your application has been submitted.</div>
            <label>I would like to register a Job Seeker account <input type="checkbox" checked /></label>
            <button aria-label="Close">X</button>
            """
        )
        page.get_by_text("Your application has been submitted.").first.wait_for(timeout=1000)
        disabled = apply_agent._disable_jobserve_account_options(page, warnings)
        closed = apply_agent._close_modal(page)

    assert disabled == ["register a Job Seeker account"]
    assert closed is True


@contextmanager
def apply_client(
    *,
    jobserve: bool = False,
    with_profile: bool = False,
    with_cv: bool = False,
    email: str = "apply-agent@example.invalid",
    dropdowns: bool = True,
    score: int = 90,
    minimum_apply_score: int = 80,
) -> Generator[tuple[TestClient, dict], None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)
    with TestingSession() as db:
        user, job = _seed_application(db, jobserve=jobserve)
        if with_profile:
            cv_path = str(Path(__file__).resolve()) if with_cv else None
            db.add(
                UserProfile(
                    user_id=user.id,
                    cv_text="CV",
                    email=email,
                    first_name="Alex",
                    last_name="Applicant",
                    phone="07000000000",
                    work_status_uk="UK citizen",
                    availability_notice="Immediate" if dropdowns else None,
                    salary_expectation_gbp=65000 if dropdowns else None,
                    travel_distance_miles=25 if dropdowns else None,
                    minimum_apply_score=minimum_apply_score,
                    cv_file_path=cv_path,
                    cv_file_name="cv.pdf" if cv_path else None,
                )
            )
            db.add(JobScore(job_id=job.id, user_id=user.id, total_score=score, recommendation="apply", recommendation_tier="Strong match"))
            db.commit()
        ids = {"user": user.id, "job": job.id, "Session": TestingSession}

    def override_get_db():
        with TestingSession() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app), ids
    finally:
        app.dependency_overrides.clear()


def _seed_application(db_session, *, url: str = "https://example.invalid/apply", jobserve: bool = False) -> tuple[User, Job]:
    user = User(email="apply-agent@example.invalid")
    source = JobSource(name=f"Apply Agent Source {id(db_session)}", base_url="https://example.invalid", source_type="fixture")
    db_session.add_all([user, source])
    db_session.flush()
    job = Job(
        source_id=source.id,
        source_job_id=f"job-{id(user)}",
        canonical_url=url,
        title="AI Engineer",
        company_name="Example Ltd",
        application_status="ready_to_apply",
        availability_status="active",
        apply_strategy="jobserve_apply_easy" if jobserve else "greenhouse",
        apply_difficulty="easy" if jobserve else "medium",
    )
    db_session.add(job)
    db_session.commit()
    return user, job


def _fake_runner(url, candidates, profile, mode, apply_strategy):
    return AssistApplyResult(status="review_required", filled_fields=["Email"], unfilled_fields=[], warnings=[], screenshot_path=None)


@contextmanager
def _playwright_page():
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Playwright unavailable: {exc}")
    try:
        manager = sync_playwright()
        playwright = manager.__enter__()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Playwright unavailable: {exc}")
    try:
        try:
            browser = playwright.chromium.launch(headless=True)
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"Chromium unavailable: {exc}")
        page = browser.new_page()
        try:
            yield page, browser
        finally:
            browser.close()
    finally:
        manager.__exit__(None, None, None)


class _FakeControl:
    def __init__(self, checked: bool) -> None:
        self.checked = checked
        self.unchecked = False

    def is_checked(self):
        return self.checked

    def uncheck(self):
        self.unchecked = True


class _FakeLocator:
    def __init__(self, controls) -> None:
        self.controls = controls

    def all(self):
        return self.controls


class _FakePage:
    def __init__(self, controls) -> None:
        self.controls = controls

    def get_by_label(self, pattern):
        return _FakeLocator(self.controls)


class _FakeSelectPage:
    def __init__(self, options: list[str] | None = None) -> None:
        self.selected = None
        self.options = options or ["Immediate", "One month"]

    def get_by_label(self, pattern):
        return _FakeSelectLocator(self)


class _FakeSelectLocator:
    def __init__(self, page: _FakeSelectPage) -> None:
        self.page = page
        self.first = self

    def evaluate(self, expression, timeout=0):
        return [
            {"index": index, "label": option, "text": option, "value": option}
            for index, option in enumerate(self.page.options)
        ]

    def select_option(self, *, label=None, value=None, index=None, timeout=0):
        if value is not None:
            self.page.selected = value
            return
        if index is not None:
            self.page.selected = self.page.options[index]
            return
        if isinstance(label, str):
            self.page.selected = label
            return
        self.page.selected = getattr(label, "pattern", str(label))
