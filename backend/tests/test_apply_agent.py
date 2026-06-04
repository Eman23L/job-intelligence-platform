from collections.abc import Generator
from contextlib import contextmanager
from datetime import timedelta
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
from app.services import applications as applications_service
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


def test_assist_apply_uses_saved_job_url(monkeypatch) -> None:
    seen: dict[str, str] = {}

    def fake_runner(url, candidates, *, profile=None, mode="review_only", apply_strategy="unknown", **kwargs):
        seen["url"] = url
        seen["strategy"] = apply_strategy
        return AssistApplyResult(
            status="review_required",
            filled_fields=[],
            unfilled_fields=[],
            unfilled_required_fields=[],
            uploaded_cv=False,
            submitted=False,
            warnings=[],
            screenshot_path=None,
        )

    monkeypatch.setattr(
        apply_agent,
        "check_job_availability",
        lambda db, candidate: SimpleNamespace(availability_status="active", availability_reason="fixture"),
    )
    monkeypatch.setattr(apply_agent, "run_playwright_assist", fake_runner)
    with apply_client(jobserve=True) as (client, ids):
        with ids["Session"]() as db:
            job = db.get(Job, ids["job"])
            job.canonical_url = "https://www.jobserve.com/gb/en/job/D8DF"
            db.commit()
        response = client.post(f"/applications/{ids['job']}/assist-apply")

    assert response.status_code == 200
    assert seen["url"] == "https://www.jobserve.com/gb/en/job/D8DF"
    assert seen["strategy"] == "jobserve_apply_easy"
    assert "Job-Search" not in seen["url"]


def test_jobserve_missing_saved_specific_url_fails_before_browser_launch(db_session, monkeypatch) -> None:
    user, job = _seed_application(db_session, jobserve=True, url="https://www.jobserve.com/gb/en/JobSearch.aspx?shid=fixture")
    called = False

    def runner(*args, **kwargs):
        nonlocal called
        called = True
        return AssistApplyResult(status="review_required")

    monkeypatch.setattr(apply_agent, "check_job_availability", lambda db, candidate: SimpleNamespace(availability_status="active", availability_reason="fixture"))

    with pytest.raises(ValueError, match="missing_saved_jobserve_url"):
        apply_agent.assist_apply_application(db_session, job, user, browser_runner=runner)

    db_session.refresh(job)
    assert called is False
    assert job.assisted_result["status"] == "failed"
    assert job.assisted_result["final_error"] == "missing_saved_jobserve_url"
    assert job.assisted_result["jobserve_flow_diagnostics"]["canonical_url"] == "https://www.jobserve.com/gb/en/JobSearch.aspx?shid=fixture"
    assert job.assisted_result["jobserve_flow_diagnostics"]["url_resolution"]["rejected_urls"][0]["reason"] == "generic_jobserve_search_url"


def test_jobserve_seo_style_url_is_accepted(db_session, monkeypatch) -> None:
    seo_url = "https://www.jobserve.com/gb/en/search-jobs-in-London,-London,-United-Kingdom/SC-CLEARED-AI-ML-ENGINEER-327CC8F570D6AB55A"
    user, job = _seed_application(db_session, jobserve=True, url=seo_url)
    job.source_job_id = "327CC8F570D6AB55A"
    db_session.commit()
    seen: dict[str, str] = {}

    def runner(url, candidates, profile, mode, apply_strategy):
        seen["url"] = url
        return AssistApplyResult(status="review_required")

    monkeypatch.setattr(apply_agent, "check_job_availability", lambda db, candidate: SimpleNamespace(availability_status="active", availability_reason="fixture"))

    apply_agent.assist_apply_application(db_session, job, user, browser_runner=runner)

    assert seen["url"] == seo_url


def test_jobserve_url_resolution_prefers_apply_url_then_canonical_url(db_session) -> None:
    apply_url = "https://www.jobserve.com/gb/en/search-jobs-in-London,-London,-United-Kingdom/APPLY-URL-327CC8F570D6AB55A"
    canonical_url = "https://www.jobserve.com/gb/en/search-jobs-in-London,-London,-United-Kingdom/CANONICAL-URL-327CC8F570D6AB55A"
    _user, job = _seed_application(db_session, jobserve=True, url=canonical_url)
    job.source_job_id = "327CC8F570D6AB55A"
    job.apply_url = apply_url
    db_session.commit()

    assert apply_agent._resolve_assist_apply_url(job) == apply_url
    diagnostics = apply_agent._resolve_assist_apply_url_diagnostics(job)
    assert diagnostics["selected_url_source"] == "apply_url"


def test_jobserve_url_resolution_reconstructs_from_source_id_only_when_needed(db_session) -> None:
    _user, job = _seed_application(db_session, jobserve=True, url="https://www.jobserve.com/gb/en/JobSearch.aspx?shid=fixture")
    job.source_job_id = "327CC8F570D6AB55A"
    db_session.commit()

    assert apply_agent._resolve_assist_apply_url(job) == "https://www.jobserve.com/gb/en/job/327CC8F570D6AB55A"


def test_browser_launch_exception_persists_failed_result(db_session, monkeypatch) -> None:
    user, job = _seed_application(db_session, jobserve=True, url="https://www.jobserve.com/gb/en/job/D8DF")

    def fake_run(*args, **kwargs):
        raise apply_agent.BrowserAutomationError("browser_startup_timeout", "Browser startup timed out after 30 seconds.")

    monkeypatch.setattr(apply_agent, "check_job_availability", lambda db, candidate: SimpleNamespace(availability_status="active", availability_reason="fixture"))
    monkeypatch.setattr(apply_agent, "run_playwright_assist", fake_run)

    with pytest.raises(apply_agent.BrowserAutomationError):
        apply_agent.assist_apply_application(db_session, job, user)

    db_session.refresh(job)
    assert job.assisted_result["status"] == "failed"
    assert job.assisted_result["final_error"] == "Browser startup timed out after 30 seconds."
    assert job.assisted_result["jobserve_flow_diagnostics"]["browser_diagnostics"]["playwright_enabled"] in {True, False}


def test_job_url_resolution_is_persisted_in_progress(db_session, monkeypatch) -> None:
    user, job = _seed_application(db_session, jobserve=True, url="https://www.jobserve.com/gb/en/job/D8DF")

    def runner(url, candidates, profile, mode, apply_strategy):
        return AssistApplyResult(status="review_required", progress={"current_step": "review_required"})

    monkeypatch.setattr(apply_agent, "check_job_availability", lambda db, candidate: SimpleNamespace(availability_status="active", availability_reason="fixture"))

    apply_agent.assist_apply_application(db_session, job, user, browser_runner=runner)

    db_session.refresh(job)
    steps = job.assisted_result["debug_steps"]
    step_names = [step["step"] for step in steps]
    assert step_names[:6] == [
        "worker_started",
        "worker_handoff_entered",
        "db_session_create_start",
        "db_session_create_done",
        "assist_apply_application_entered",
        "loading_application",
    ]
    assert any(step["step"] == "application_loaded" for step in steps)
    assert any(step["step"] == "profile_loaded" for step in steps)
    resolved = next(step for step in steps if step["step"] == "job_url_resolved")
    assert resolved["resolved_job_url"] == "https://www.jobserve.com/gb/en/job/D8DF"


def test_application_load_timeout_is_persisted(db_session, monkeypatch) -> None:
    user, job = _seed_application(db_session, jobserve=True)
    original_persist = apply_agent._persist_assist_progress

    def slow_persist(*args, **kwargs):
        time.sleep(0.002)
        return original_persist(*args, **kwargs)

    monkeypatch.setattr(apply_agent, "DB_LOOKUP_TIMEOUT_SECONDS", 0.001)
    monkeypatch.setattr(apply_agent, "_persist_assist_progress", slow_persist)

    with pytest.raises(TimeoutError, match="application_load_timeout"):
        apply_agent.assist_apply_application(db_session, job, user, browser_runner=_fake_runner)

    db_session.refresh(job)
    assert job.assisted_result["status"] == "failed"
    assert job.assisted_result["final_error"] == "application_load_timeout"
    assert job.assisted_result["progress"]["current_step"] == "loading_application"


def test_database_lookup_error_is_persisted(db_session, monkeypatch) -> None:
    from sqlalchemy.exc import SQLAlchemyError

    user, job = _seed_application(db_session, jobserve=True)

    def fail_availability():
        raise SQLAlchemyError("database unavailable")

    monkeypatch.setattr(apply_agent, "check_job_availability", lambda db, candidate: fail_availability())

    with pytest.raises(RuntimeError, match="database_lookup_error"):
        apply_agent.assist_apply_application(db_session, job, user, browser_runner=_fake_runner)

    db_session.refresh(job)
    assert job.assisted_result["status"] == "failed"
    assert job.assisted_result["final_error"] == "database_lookup_error"
    assert job.assisted_result["exceptions"][0]["type"] == "RuntimeError"


def test_exception_after_worker_started_is_persisted_with_traceback(db_session, monkeypatch) -> None:
    user, job = _seed_application(db_session, jobserve=True)
    original_persist = apply_agent._persist_assist_progress

    def fail_after_worker_started(db, candidate_job, step, payload, started_perf):
        if step == "worker_handoff_entered":
            raise RuntimeError("handoff exploded")
        return original_persist(db, candidate_job, step, payload, started_perf)

    monkeypatch.setattr(apply_agent, "_persist_assist_progress", fail_after_worker_started)

    with pytest.raises(RuntimeError, match="handoff exploded"):
        apply_agent.assist_apply_application(db_session, job, user, browser_runner=_fake_runner)

    db_session.refresh(job)
    assert job.assisted_result["status"] == "failed"
    assert job.assisted_result["final_error"] == "handoff exploded"
    assert job.assisted_result["running_step"] == "worker_started"
    assert job.assisted_result["exceptions"][-1]["type"] == "RuntimeError"
    assert "handoff exploded" in job.assisted_result["exceptions"][-1]["traceback"]


def test_exception_during_jobserve_fill_persists_real_error(db_session, monkeypatch) -> None:
    user, job = _seed_application(db_session, jobserve=True, url="https://www.jobserve.com/gb/en/job/D8DF")
    monkeypatch.setattr(apply_agent, "check_job_availability", lambda db, candidate: SimpleNamespace(availability_status="active", availability_reason="fixture"))

    def fail_during_fill(*args, **kwargs):
        progress_callback = kwargs["progress_callback"]
        progress_callback("before_filling", {"form_fields_detected": 15, "cv_upload_input_detected": True})
        progress_callback("email_filled", {"succeeded": False})
        raise RuntimeError("email fill exploded")

    monkeypatch.setattr(apply_agent, "run_playwright_assist", fail_during_fill)

    with pytest.raises(ValueError, match="email fill exploded"):
        apply_agent.assist_apply_application(db_session, job, user, debug_mode=True)

    db_session.refresh(job)
    assert job.assisted_result["status"] == "failed"
    assert job.assisted_result["final_error"] == "email fill exploded"
    assert job.assisted_result["exceptions"][-1]["message"] == "email fill exploded"
    assert any(step["step"] == "before_filling" for step in job.assisted_result["debug_steps"])
    assert any(step["step"] == "email_filled" for step in job.assisted_result["debug_steps"])


def test_store_assist_failure_preserves_progress_payload(db_session) -> None:
    user, job = _seed_application(db_session, jobserve=True)
    job.assisted_result = {
        "status": "running",
        "filled_fields": ["Email", "CV upload"],
        "uploaded_cv": True,
        "debug_steps": [{"step": "final_submit_click", "status": "start"}],
        "screenshot_paths": ["before_submit.jpg"],
        "html_snapshot_paths": ["before_submit.html"],
        "progress": {"current_step": "final_submit_click"},
    }
    db_session.commit()

    apply_agent._store_assist_failure(
        db_session,
        job,
        "Final JobServe Apply button could not be clicked.",
        error="final_submit_click",
        running_step="final_submit_click",
    )

    db_session.refresh(job)
    result = job.assisted_result
    assert result["status"] == "failed"
    assert result["filled_fields"] == ["Email", "CV upload"]
    assert result["uploaded_cv"] is True
    assert result["running_step"] == "final_submit_click"
    assert result["progress"]["current_step"] == "final_submit_click"
    assert result["debug_steps"] == [{"step": "final_submit_click", "status": "start"}]


def test_jobserve_apply_button_prefers_visible_apply_over_hidden_nojs() -> None:
    class FakeButton:
        def __init__(self, *, text: str = "", value: str = "", visible: bool = True, element_id: str = "") -> None:
            self.text = text
            self.value = value
            self.visible = visible
            self.element_id = element_id

        def is_visible(self, timeout=None):
            return self.visible

        def inner_text(self, timeout=None):
            return self.text

        def get_attribute(self, name):
            return {"value": self.value, "aria-label": "", "id": self.element_id}.get(name)

    class FakeLocator:
        def __init__(self, buttons):
            self._buttons = buttons

        def all(self):
            return self._buttons

        @property
        def first(self):
            return self._buttons[0]

    class FakePage:
        def __init__(self) -> None:
            self.hidden_nojs = FakeButton(value="Apply", visible=False, element_id="btn2NoJS")
            self.visible_apply = FakeButton(value="Apply", visible=True, element_id="btnApply")

        def get_by_role(self, *_args, **_kwargs):
            return FakeLocator([])

        def locator(self, selector):
            if selector == 'input[type=submit][value="Apply"]:visible:not(#btn2NoJS)':
                return FakeLocator([self.visible_apply])
            if selector == 'input[type=submit]:visible':
                return FakeLocator([self.hidden_nojs])
            return FakeLocator([])

    page = FakePage()

    assert apply_agent._jobserve_apply_button(page) is page.visible_apply


def test_worker_early_crash_does_not_leave_application_running(monkeypatch) -> None:
    with apply_client(jobserve=True) as (_client, ids):
        monkeypatch.setattr(apply_agent, "SessionLocal", ids["Session"])

        def crash(*args, **kwargs):
            raise RuntimeError("early crash")

        monkeypatch.setattr(apply_agent, "assist_apply_application", crash)
        apply_agent.run_assist_apply_background(ids["job"], ids["user"], "review_only", False)

        with ids["Session"]() as db:
            job = db.get(Job, ids["job"])
            assert job.assisted_result["status"] == "failed"
            assert job.assisted_result["final_error"] == "early crash"
            assert job.assisted_result["progress"]["current_step"] != "worker_started"


def test_worker_missing_application_logs_not_found(monkeypatch, caplog) -> None:
    with apply_client(jobserve=True) as (_client, ids):
        monkeypatch.setattr(apply_agent, "SessionLocal", ids["Session"])

        apply_agent.run_assist_apply_background(999999, ids["user"], "review_only", False)

    assert "application_not_found" in caplog.text
    assert "job_not_found" in caplog.text


def test_worker_db_session_is_closed_on_success_and_failure(monkeypatch) -> None:
    class FakeSession:
        def __init__(self, fail=False):
            self.fail = fail
            self.closed = False

        def get(self, model, ident):
            if self.fail:
                raise RuntimeError("db exploded")
            return None

        def close(self):
            self.closed = True

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            self.close()
            return False

    created = [FakeSession(), FakeSession(fail=True)]
    pending = list(created)

    def session_factory():
        return pending.pop(0)

    monkeypatch.setattr(apply_agent, "SessionLocal", session_factory)
    apply_agent.run_assist_apply_background(999999, 1, "review_only", False)
    apply_agent.run_assist_apply_background(999999, 1, "review_only", False)

    assert all(session.closed for session in created)


def test_db_session_creation_timeout_is_persisted(monkeypatch) -> None:
    with apply_client(jobserve=True) as (_client, ids):
        calls = {"count": 0}

        def session_factory():
            calls["count"] += 1
            if calls["count"] == 1:
                time.sleep(0.02)
            return ids["Session"]()

        monkeypatch.setattr(apply_agent, "SessionLocal", session_factory)
        monkeypatch.setattr(apply_agent, "DB_SESSION_CREATE_TIMEOUT_SECONDS", 0.001)

        with pytest.raises(TimeoutError, match="db_session_create_timeout"):
            apply_agent._create_db_session_with_timeout(ids["job"], ids["user"], "review_only", False, "assist-123")

        with ids["Session"]() as db:
            job = db.get(Job, ids["job"])
            assert job.assisted_result["status"] == "failed"
            assert job.assisted_result["final_error"] == "db_session_create_timeout"
            assert job.assisted_result["running_step"] == "db_session_create_start"
            assert job.assisted_result["exceptions"][-1]["type"] == "TimeoutError"


def test_worker_startup_warns_when_service_type_is_web(monkeypatch, caplog) -> None:
    from app import worker

    caplog.set_level("INFO")

    class FakeConnection:
        def ping(self):
            return True

    class FakeWorker:
        def __init__(self, *args, **kwargs):
            pass

        def work(self, *args, **kwargs):
            return False

    monkeypatch.setattr(worker.settings, "service_type", "web")
    monkeypatch.setattr(worker, "redis_connection", lambda: FakeConnection())
    monkeypatch.setattr(worker, "browser_status", lambda: {"playwright_installed": True, "chromium_available": True, "worker_running": False})
    monkeypatch.setattr(
        worker,
        "chromium_diagnostics",
        lambda: {
            "playwright_browsers_path": None,
            "chromium_executable_path": None,
            "chromium_path_source": None,
            "chromium_file_exists": False,
            "chromium_file_executable": False,
            "ms_playwright_listing": [],
        },
    )
    monkeypatch.setattr(worker, "Worker", FakeWorker)

    worker.main()

    assert "worker_service_type_misconfigured" in caplog.text
    assert "worker_work_start" in caplog.text


def test_worker_startup_runs_with_service_type_worker_without_port(monkeypatch, caplog) -> None:
    from app import worker

    caplog.set_level("INFO")
    monkeypatch.delenv("PORT", raising=False)
    work_called = {"value": False}

    class FakeConnection:
        def ping(self):
            return True

    class FakeWorker:
        def __init__(self, queues, connection):
            self.queues = queues
            self.connection = connection

        def work(self, *args, **kwargs):
            work_called["value"] = True
            return False

    monkeypatch.setattr(worker.settings, "service_type", "worker")
    monkeypatch.setattr(worker, "redis_connection", lambda: FakeConnection())
    monkeypatch.setattr(worker, "browser_status", lambda: {"playwright_installed": True, "chromium_available": True, "worker_running": False})
    monkeypatch.setattr(
        worker,
        "chromium_diagnostics",
        lambda: {
            "playwright_browsers_path": "0",
            "chromium_executable_path": "/chromium",
            "chromium_path_source": "playwright_api",
            "chromium_file_exists": True,
            "chromium_file_executable": True,
            "ms_playwright_listing": [],
        },
    )
    monkeypatch.setattr(worker, "Worker", FakeWorker)

    worker.main()

    assert work_called["value"] is True
    assert "worker_boot service_type=worker" in caplog.text
    assert "worker_construction_start" in caplog.text
    assert "worker_work_start" in caplog.text
    assert "PORT" not in caplog.text


def test_worker_invalid_redis_logs_clear_startup_error(monkeypatch, caplog) -> None:
    from app import worker

    caplog.set_level("INFO")

    class FakeConnection:
        def ping(self):
            raise RuntimeError("redis ping failed")

    monkeypatch.setattr(worker.settings, "service_type", "worker")
    monkeypatch.setattr(worker, "redis_connection", lambda: FakeConnection())

    with pytest.raises(RuntimeError, match="redis ping failed"):
        worker.main()

    assert "worker_redis_ping_start" in caplog.text
    assert "worker_startup_failed" in caplog.text
    assert "redis ping failed" in caplog.text


def test_render_worker_service_configuration() -> None:
    render_yaml = Path(__file__).resolve().parents[2] / "render.yaml"
    content = render_yaml.read_text(encoding="utf-8")

    assert "name: job-intelligence-backend" in content
    assert "SERVICE_TYPE\n        value: web" in content
    assert "name: job-intelligence-worker" in content
    assert "startCommand: PYTHONPATH=backend python -m app.worker" in content
    assert "SERVICE_TYPE\n        value: worker" in content


def test_worker_missing_user_persists_failure(monkeypatch) -> None:
    with apply_client(jobserve=True) as (_client, ids):
        monkeypatch.setattr(apply_agent, "SessionLocal", ids["Session"])

        apply_agent.run_assist_apply_background(ids["job"], 999999, "review_only", False)

        with ids["Session"]() as db:
            job = db.get(Job, ids["job"])
            assert job.assisted_result["status"] == "failed"
            assert job.assisted_result["final_error"] == "user_not_found"
            assert job.assisted_result["jobserve_flow_diagnostics"]["db_lookup"]["job_found"] is True


def test_submit_missing_profile_fails_with_profile_not_found(monkeypatch) -> None:
    monkeypatch.setattr(apply_agent, "check_job_availability", lambda db, candidate: SimpleNamespace(availability_status="active", availability_reason="fixture"))
    with apply_client(jobserve=True, with_profile=False) as (client, ids):
        response = client.post(f"/applications/{ids['job']}/assist-apply", json={"mode": "submit_with_confirmation"})
        with ids["Session"]() as db:
            job = db.get(Job, ids["job"])

    assert response.status_code == 400
    assert response.json()["detail"] == "profile_not_found"
    assert job.assisted_result["status"] == "failed"
    assert job.assisted_result["jobserve_flow_diagnostics"]["db_lookup"]["profile_found"] is False
    assert job.assisted_result["jobserve_flow_diagnostics"]["db_lookup"]["cv_found"] is False


def test_profile_lookup_exception_is_persisted_with_traceback(db_session, monkeypatch) -> None:
    user, job = _seed_application(db_session, jobserve=True)
    monkeypatch.setattr(apply_agent, "check_job_availability", lambda db, candidate: SimpleNamespace(availability_status="active", availability_reason="fixture"))

    def fail_profile(db, candidate_user):
        raise RuntimeError("profile database failed")

    monkeypatch.setattr(apply_agent, "get_profile", fail_profile)

    with pytest.raises(ValueError, match="profile database failed"):
        apply_agent.assist_apply_application(db_session, job, user, browser_runner=_fake_runner)

    db_session.refresh(job)
    assert job.assisted_result["status"] == "failed"
    assert job.assisted_result["progress"]["current_step"] == "loading_profile"
    assert job.assisted_result["exceptions"][0]["type"] == "RuntimeError"
    assert "profile database failed" in job.assisted_result["exceptions"][0]["traceback"]
    assert job.assisted_result["jobserve_flow_diagnostics"]["db_lookup"]["profile_found"] is False


def test_stale_browser_launch_progress_fails_on_applications_list(db_session) -> None:
    user, job = _seed_application(db_session, jobserve=True)
    job.assisted_result = {
        "status": "running",
        "warnings": [],
        "progress": {"current_step": "browser_launch_start", "message": "browser startup"},
    }
    job.last_apply_attempt_at = apply_agent.utcnow() - timedelta(seconds=31)
    db_session.commit()

    applications_service.list_applications(db_session, user)

    db_session.refresh(job)
    assert job.assisted_result["status"] == "failed"
    assert job.assisted_result["final_error"] == "browser_startup_timeout"
    assert job.assisted_result["progress"]["current_step"] == "browser_startup_timeout"


def test_stale_queued_assist_is_reported_on_applications_list(db_session, monkeypatch) -> None:
    user, job = _seed_application(db_session, jobserve=True)
    job.assisted_result = {
        "status": "queued",
        "warnings": [],
        "progress": {"current_step": "queued", "message": "queued", "rq_job_id": "assist-123"},
        "jobserve_flow_diagnostics": {"rq_job_id": "assist-123"},
    }
    job.last_apply_attempt_at = apply_agent.utcnow() - timedelta(minutes=3)
    db_session.commit()
    monkeypatch.setattr(applications_service, "rq_job_failure", lambda rq_job_id: None)

    applications_service.list_applications(db_session, user)

    db_session.refresh(job)
    assert job.assisted_result["status"] == "failed"
    assert job.assisted_result["final_error"] == "stale_queue_timeout"
    assert job.assisted_result["progress"]["current_step"] == "stale_queue_timeout"
    assert job.assisted_result["jobserve_flow_diagnostics"]["queue_failure"]["rq_job_id"] == "assist-123"
    assert job.assisted_result["jobserve_flow_diagnostics"]["queue_diagnostics"]["worker_progress_seen"] is False


def test_queued_assist_with_worker_started_is_not_marked_stale(db_session, monkeypatch) -> None:
    user, job = _seed_application(db_session, jobserve=True)
    job.assisted_result = {
        "status": "queued",
        "warnings": [],
        "progress": {"current_step": "worker_started", "message": "worker started", "rq_job_id": "assist-123", "last_heartbeat_at": apply_agent.utcnow().isoformat()},
        "debug_steps": [{"step": "queued"}, {"step": "worker_started"}],
        "jobserve_flow_diagnostics": {"rq_job_id": "assist-123"},
    }
    job.last_apply_attempt_at = apply_agent.utcnow() - timedelta(minutes=3)
    db_session.commit()
    monkeypatch.setattr(applications_service, "rq_job_failure", lambda rq_job_id: None)

    applications_service.list_applications(db_session, user)

    db_session.refresh(job)
    assert job.assisted_result["status"] == "queued"
    assert job.assisted_result.get("final_error") is None
    assert "queue_failure" not in job.assisted_result["jobserve_flow_diagnostics"]


def test_worker_started_handoff_timeout_fails_on_applications_list(db_session, monkeypatch) -> None:
    user, job = _seed_application(db_session, jobserve=True)
    stale_heartbeat = (apply_agent.utcnow() - timedelta(seconds=31)).isoformat()
    job.assisted_result = {
        "status": "running",
        "warnings": [],
        "progress": {"current_step": "worker_started", "message": "worker started", "rq_job_id": "assist-123", "last_heartbeat_at": stale_heartbeat},
        "debug_steps": [{"step": "queued"}, {"step": "worker_started"}],
        "jobserve_flow_diagnostics": {"rq_job_id": "assist-123"},
    }
    job.last_apply_attempt_at = apply_agent.utcnow() - timedelta(minutes=3)
    db_session.commit()
    monkeypatch.setattr(applications_service, "rq_job_failure", lambda rq_job_id: None)

    applications_service.list_applications(db_session, user)

    db_session.refresh(job)
    assert job.assisted_result["status"] == "failed"
    assert job.assisted_result["final_error"] == "worker_startup_handoff_timeout"
    assert job.assisted_result["running_step"] == "worker_startup_handoff_timeout"
    assert job.assisted_result["jobserve_flow_diagnostics"]["queue_diagnostics"]["last_successful_step"] == "worker_started"


def test_queued_assist_with_jobserve_debug_steps_is_not_marked_stale(db_session, monkeypatch) -> None:
    user, job = _seed_application(db_session, jobserve=True)
    job.assisted_result = {
        "status": "queued",
        "warnings": [],
        "progress": {"current_step": "queued", "message": "queued", "rq_job_id": "assist-123", "last_heartbeat_at": apply_agent.utcnow().isoformat()},
        "debug_steps": [
            {"step": "queued"},
            {"step": "apply_button_clicked"},
            {"step": "modal_wait_complete", "job_application_modal_found": True},
            {"step": "before_filling", "form_fields_detected": 15, "cv_upload_input_detected": True},
        ],
        "jobserve_flow_diagnostics": {"rq_job_id": "assist-123", "job_application_modal_found": True, "cv_upload_input_detected": True},
    }
    job.last_apply_attempt_at = apply_agent.utcnow() - timedelta(minutes=3)
    db_session.commit()
    monkeypatch.setattr(applications_service, "rq_job_failure", lambda rq_job_id: None)

    applications_service.list_applications(db_session, user)

    db_session.refresh(job)
    assert job.assisted_result["status"] == "queued"
    assert job.assisted_result.get("final_error") is None
    assert job.assisted_result["debug_steps"][-1]["step"] == "before_filling"
    assert "queue_failure" not in job.assisted_result["jobserve_flow_diagnostics"]


def test_stale_checker_does_not_overwrite_worker_result(db_session, monkeypatch) -> None:
    user, job = _seed_application(db_session, jobserve=True)
    job.assisted_result = {
        "status": "failed",
        "final_error": "modal_fill_failed",
        "warnings": ["modal_fill_failed"],
        "progress": {"current_step": "before_filling", "message": "filling modal", "rq_job_id": "assist-123", "last_heartbeat_at": apply_agent.utcnow().isoformat()},
        "debug_steps": [{"step": "apply_button_clicked"}, {"step": "modal_wait_complete"}, {"step": "before_filling"}],
        "jobserve_flow_diagnostics": {"rq_job_id": "assist-123", "job_application_modal_found": True},
    }
    job.last_apply_attempt_at = apply_agent.utcnow() - timedelta(minutes=3)
    db_session.commit()
    monkeypatch.setattr(applications_service, "rq_job_failure", lambda rq_job_id: None)

    applications_service.list_applications(db_session, user)

    db_session.refresh(job)
    assert job.assisted_result["status"] == "failed"
    assert job.assisted_result["final_error"] == "modal_fill_failed"
    assert "queue_failure" not in job.assisted_result["jobserve_flow_diagnostics"]


def test_stale_worker_progress_gets_worker_progress_timeout(db_session, monkeypatch) -> None:
    user, job = _seed_application(db_session, jobserve=True)
    stale_heartbeat = (apply_agent.utcnow() - timedelta(minutes=20)).isoformat()
    job.assisted_result = {
        "status": "running",
        "warnings": [],
        "progress": {"current_step": "before_filling", "message": "filling modal", "rq_job_id": "assist-123", "last_heartbeat_at": stale_heartbeat},
        "debug_steps": [{"step": "apply_button_clicked"}, {"step": "modal_wait_complete"}, {"step": "before_filling"}],
        "jobserve_flow_diagnostics": {"rq_job_id": "assist-123", "job_application_modal_found": True},
    }
    job.last_apply_attempt_at = apply_agent.utcnow() - timedelta(minutes=20)
    db_session.commit()
    monkeypatch.setattr(applications_service, "rq_job_failure", lambda rq_job_id: None)

    applications_service.list_applications(db_session, user)

    db_session.refresh(job)
    assert job.assisted_result["status"] == "failed"
    assert job.assisted_result["final_error"] == "worker_progress_timeout"
    assert job.assisted_result["jobserve_flow_diagnostics"]["queue_diagnostics"]["worker_progress_seen"] is True


def test_jobserve_fill_steps_prevent_stale_queue_timeout(db_session, monkeypatch) -> None:
    user, job = _seed_application(db_session, jobserve=True)
    job.assisted_result = {
        "status": "queued",
        "warnings": [],
        "progress": {"current_step": "queued", "message": "queued", "rq_job_id": "assist-123", "last_heartbeat_at": apply_agent.utcnow().isoformat()},
        "debug_steps": [{"step": "email_filled"}, {"step": "cv_uploaded"}, {"step": "final_apply_clicked"}],
        "jobserve_flow_diagnostics": {"rq_job_id": "assist-123"},
    }
    job.last_apply_attempt_at = apply_agent.utcnow() - timedelta(minutes=3)
    db_session.commit()
    monkeypatch.setattr(applications_service, "rq_job_failure", lambda rq_job_id: None)

    applications_service.list_applications(db_session, user)

    db_session.refresh(job)
    assert job.assisted_result["status"] == "queued"
    assert job.assisted_result.get("final_error") is None
    assert "queue_failure" not in job.assisted_result["jobserve_flow_diagnostics"]


def test_worker_shutdown_after_before_filling_persists_failure(monkeypatch) -> None:
    with apply_client(jobserve=True) as (_client, ids):
        monkeypatch.setattr(apply_agent, "SessionLocal", ids["Session"])
        with ids["Session"]() as db:
            job = db.get(Job, ids["job"])
            job.assisted_result = {
                "status": "running",
                "warnings": [],
                "progress": {"current_step": "before_filling", "message": "filling modal", "rq_job_id": "assist-123"},
                "debug_steps": [{"step": "apply_button_clicked"}, {"step": "modal_wait_complete"}, {"step": "before_filling"}],
                "jobserve_flow_diagnostics": {"rq_job_id": "assist-123", "job_application_modal_found": True},
            }
            db.commit()

        apply_agent._persist_worker_shutdown_during_apply(
            ids["job"],
            {"user_id": ids["user"], "mode": "submit_with_confirmation", "debug_mode": True, "rq_job_id": "assist-123"},
        )

        with ids["Session"]() as db:
            job = db.get(Job, ids["job"])
            assert job.assisted_result["status"] == "failed"
            assert job.assisted_result["final_error"] == "worker_shutdown_during_apply"
            assert job.assisted_result["progress"]["current_step"] == "before_filling"
            assert job.assisted_result["debug_steps"][-1]["step"] == "before_filling"


def test_applications_list_does_not_call_rq_failure_check(db_session, monkeypatch) -> None:
    user, job = _seed_application(db_session, jobserve=True)
    job.assisted_result = {
        "status": "queued",
        "warnings": [],
        "progress": {"current_step": "queued", "message": "queued", "rq_job_id": "assist-123"},
        "jobserve_flow_diagnostics": {"rq_job_id": "assist-123"},
    }
    job.last_apply_attempt_at = apply_agent.utcnow()
    db_session.commit()
    called = []
    monkeypatch.setattr(applications_service, "rq_job_failure", lambda rq_job_id: called.append(rq_job_id))

    applications_service.list_applications(db_session, user)

    db_session.refresh(job)
    assert called == []
    assert job.assisted_result["status"] == "queued"
    assert "queue_failure" not in job.assisted_result["jobserve_flow_diagnostics"]


def test_applications_endpoint_does_not_invoke_playwright(monkeypatch) -> None:
    monkeypatch.setattr(apply_agent, "run_playwright_assist", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("playwright should not run")))
    with apply_client(jobserve=True) as (client, _ids):
        response = client.get("/applications")

    assert response.status_code == 200


def test_availability_check_is_not_treated_as_db_job_load_timeout(db_session, monkeypatch) -> None:
    import time

    user, job = _seed_application(db_session, jobserve=True)

    def slow_availability(db, candidate):
        time.sleep(0.02)
        return SimpleNamespace(availability_status="active", availability_reason="fixture")

    monkeypatch.setattr(apply_agent, "check_job_availability", slow_availability)
    monkeypatch.setattr(apply_agent, "DB_LOOKUP_TIMEOUT_SECONDS", 0.001)

    result = apply_agent._timed_availability_check(db_session, job)

    assert result.availability_status == "active"


def test_applications_endpoint_returns_cached_data_on_list_failure(db_session, monkeypatch) -> None:
    user, _job = _seed_application(db_session, jobserve=True)
    applications_api._APPLICATIONS_CACHE = None

    first = applications_api.get_applications(db_session)
    monkeypatch.setattr(applications_api, "list_applications", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("slow query timeout")))
    second = applications_api.get_applications(db_session)

    assert first.items
    assert second.items
    assert second.warning
    assert "stale" in second.warning


def test_autonomous_canary_endpoint_queues_background_work(db_session, monkeypatch) -> None:
    from fastapi import BackgroundTasks

    user, _job = _seed_application(db_session, jobserve=True)
    enqueued = []
    monkeypatch.setattr(applications_api, "queue_enabled", lambda: True)
    monkeypatch.setattr(applications_api, "enqueue_or_background", lambda background_tasks, func, *args, **kwargs: enqueued.append((func, args, kwargs)) or "rq-canary")

    result = applications_api.run_autonomous_real_submit(BackgroundTasks(), db_session)

    assert result.status == "queued"
    assert result.recommended_fix == "Autonomous canary queued. Background run still processing."
    assert enqueued
    assert enqueued[0][0] is applications_api.run_autonomous_real_submit_canary_background
    assert enqueued[0][1][0] == user.id
    assert str(enqueued[0][2]["job_id"]).startswith(f"autonomous-real-submit-{user.id}-")


def test_assist_apply_endpoint_queues_when_queue_enabled(monkeypatch) -> None:
    enqueued = []
    monkeypatch.setattr(applications_api, "queue_enabled", lambda: True)
    monkeypatch.setattr(applications_api, "redis_url_host", lambda: "redis.internal")
    monkeypatch.setattr(
        applications_api,
        "enqueue_or_background",
        lambda background_tasks, func, *args, **kwargs: enqueued.append((func, args, kwargs)) or "rq-job",
    )

    with apply_client() as (client, ids):
        response = client.post(f"/applications/{ids['job']}/assist-apply")

    assert response.status_code == 200
    assert response.json()["status"] == "queued"
    assert response.json()["progress"]["rq_job_id"] == "rq-job"
    assert response.json()["progress"]["queue_name"] == apply_agent.settings.queue_name
    assert response.json()["progress"]["redis_host"] == "redis.internal"
    assert enqueued
    assert enqueued[0][0] is apply_agent.run_assist_apply_background
    assert enqueued[0][1][0] == ids["job"]


def test_assist_apply_endpoint_queues_debug_mode(monkeypatch) -> None:
    enqueued = []
    monkeypatch.setattr(applications_api, "queue_enabled", lambda: True)
    monkeypatch.setattr(applications_api, "redis_url_host", lambda: "redis.internal")
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


def test_submit_jobserve_endpoint_queues_submit_with_confirmation(monkeypatch, caplog) -> None:
    enqueued = []
    caplog.set_level("INFO")
    monkeypatch.setattr(applications_api, "queue_enabled", lambda: True)
    monkeypatch.setattr(applications_api, "redis_url_host", lambda: "redis.internal")
    monkeypatch.setattr(
        applications_api,
        "enqueue_or_background",
        lambda background_tasks, func, *args, **kwargs: enqueued.append((func, args, kwargs)) or "rq-submit-job",
    )

    with apply_client(jobserve=True, with_profile=True, with_cv=True) as (client, ids):
        response = client.post(f"/applications/{ids['job']}/assist-apply", json={"mode": "submit_with_confirmation", "debug_mode": True})

    assert response.status_code == 200
    assert response.json()["status"] == "queued"
    assert response.json()["progress"]["rq_job_id"] == "rq-submit-job"
    assert enqueued[0][0] is apply_agent.run_assist_apply_background
    assert enqueued[0][1] == (ids["job"], ids["user"], "submit_with_confirmation", True)
    assert enqueued[0][2]["job_timeout"] == apply_agent.settings.apply_timeout_seconds
    assert "assist_apply_enqueue_start" in caplog.text
    assert "assist_apply_queued" in caplog.text
    assert "mode=submit_with_confirmation" in caplog.text
    assert "queue_enabled=True" in caplog.text
    assert "queue_name=default" in caplog.text
    assert "redis_host=redis.internal" in caplog.text
    assert "rq_job_id=rq-submit-job" in caplog.text
    assert "enqueue_success=true" in caplog.text


def test_assist_apply_enqueue_failure_logs_and_persists(monkeypatch, caplog) -> None:
    caplog.set_level("INFO")
    monkeypatch.setattr(applications_api, "queue_enabled", lambda: True)
    monkeypatch.setattr(applications_api, "redis_url_host", lambda: "redis.internal")

    def fail_enqueue(*args, **kwargs):
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr(applications_api, "enqueue_or_background", fail_enqueue)

    with apply_client(jobserve=True, with_profile=True) as (client, ids):
        response = client.post(f"/applications/{ids['job']}/assist-apply", json={"mode": "submit_with_confirmation"})
        with ids["Session"]() as db:
            job = db.get(Job, ids["job"])

    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "assist_apply_enqueue_failed"
    assert job.assisted_result["status"] == "failed"
    assert job.assisted_result["final_error"] == "assist_apply_enqueue_failed"
    assert job.assisted_result["jobserve_flow_diagnostics"]["queue_diagnostics"]["enqueue_success"] is False
    assert "assist_apply_enqueue_failed" in caplog.text
    assert "enqueue_success=false" in caplog.text
    assert "redis unavailable" in caplog.text


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


def test_user_email_fallback_allows_submit(monkeypatch) -> None:
    monkeypatch.setattr(apply_agent, "check_job_availability", lambda db, candidate: SimpleNamespace(availability_status="active", availability_reason="fixture"))
    monkeypatch.setattr(
        apply_agent,
        "run_playwright_assist",
        lambda *args, **kwargs: AssistApplyResult(status="submitted", uploaded_cv=True, submitted=True, confirmation_text="Your application has been submitted."),
    )
    with apply_client(jobserve=True, with_profile=True, with_cv=True, email="") as (client, ids):
        response = client.post(f"/applications/{ids['job']}/assist-apply", json={"mode": "submit_with_confirmation"})

    assert response.status_code == 200


def test_missing_profile_and_user_email_blocks_submit(monkeypatch) -> None:
    monkeypatch.setattr(apply_agent, "check_job_availability", lambda db, candidate: SimpleNamespace(availability_status="active", availability_reason="fixture"))
    with apply_client(jobserve=True, with_profile=True, with_cv=True, email="") as (client, ids):
        with ids["Session"]() as db:
            user = db.get(User, ids["user"])
            user.email = ""
            db.commit()
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
            confirmation_text="Your application has been submitted.",
            registration_toggle_disabled=True,
            modal_closed=True,
            submitted_job_title="AI Engineer",
        )

    monkeypatch.setattr(apply_agent, "check_job_availability", lambda db, candidate: SimpleNamespace(availability_status="active", availability_reason="fixture"))
    monkeypatch.setattr(apply_agent, "run_playwright_assist", fake_runner)
    with apply_client(jobserve=True, with_profile=True, with_cv=True) as (client, ids):
        response = client.post(f"/applications/{ids['job']}/assist-apply", json={"mode": "submit_with_confirmation"})
        with ids["Session"]() as db:
            job = db.get(Job, ids["job"])

    assert response.status_code == 200
    assert response.json()["submitted"] is True
    assert response.json()["applied_at"] is not None
    assert response.json()["confirmation_text"] == "Your application has been submitted."
    assert response.json()["registration_toggle_disabled"] is True
    assert response.json()["modal_closed"] is True
    assert response.json()["submitted_job_title"] == "AI Engineer"
    assert job.application_status == "applied"
    assert job.applied_at is not None


def test_account_registration_toggles_are_disabled_if_present() -> None:
    warnings = []
    controls = [_FakeControl(True), _FakeControl(False)]
    apply_agent._disable_jobserve_account_options(_FakePage(controls), warnings)

    assert controls[0].unchecked is True
    assert controls[1].unchecked is False
    assert any("Disabled option" in warning for warning in warnings)


def test_account_registration_dom_disable_does_not_pass_evaluate_timeout() -> None:
    class EvaluateOnlyPage:
        frames = []
        main_frame = None

        def evaluate(self, script, phrases):
            assert isinstance(script, str)
            assert "register a Job Seeker account" in phrases
            return ["register a Job Seeker account"]

    warnings: list[str] = []

    disabled = apply_agent._disable_jobserve_account_options(EvaluateOnlyPage(), warnings)

    assert disabled == ["register a Job Seeker account"]
    assert warnings == ["Disabled option: register a Job Seeker account."]


def test_jobserve_generated_email_field_is_filled_without_label() -> None:
    class GeneratedEmailLocator:
        value = ""

        def count(self):
            return 1

        def fill(self, value, timeout=None):
            self.value = value

    class GeneratedEmailPage:
        def __init__(self):
            self.generated = GeneratedEmailLocator()

        def get_by_label(self, pattern):
            raise RuntimeError("no label")

        def locator(self, selector):
            if selector == 'input#Q0006_ans':
                return SimpleNamespace(first=self.generated)
            return SimpleNamespace(first=SimpleNamespace(count=lambda: 0, fill=lambda *args, **kwargs: None))

    page = GeneratedEmailPage()
    label = apply_agent._fill_jobserve_email_field(page, apply_agent.FieldCandidate("email", "user@example.com", "fixture"))

    assert label == 'input#Q0006_ans'
    assert page.generated.value == "user@example.com"


def test_jobserve_generated_confirmation_checkbox_is_checked() -> None:
    class GeneratedCheckbox:
        checked = False

        def count(self):
            return 1

        def is_checked(self, timeout=None):
            return self.checked

        def check(self, timeout=None):
            self.checked = True

    class GeneratedCheckboxPage:
        def __init__(self):
            self.checkbox = GeneratedCheckbox()

        def get_by_label(self, pattern):
            raise RuntimeError("no label")

        def get_by_text(self, pattern):
            raise RuntimeError("no text")

        def locator(self, selector):
            if selector == 'input[type="checkbox"][name*="rptAppMand"][name*="ctl04"]':
                return SimpleNamespace(first=self.checkbox)
            return SimpleNamespace(first=SimpleNamespace(count=lambda: 0))

    flow = {}
    apply_agent._ensure_confirmation_email_checked(GeneratedCheckboxPage(), flow)

    assert flow["confirmation_email_checked"] is True
    assert flow["confirmation_checkbox_diagnostic"]["strategy"] == "jobserve_generated_selector"


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


def test_jobserve_auto_selected_identity_matching_allows_intended_job() -> None:
    intended = {"title": "Senior AI Engineer", "company_name": "Acme", "source_job_id": "D8DF"}
    auto_selected = {"title": "Senior AI Engineer", "company": "Acme", "reference": "D8DF", "text": "Senior AI Engineer Acme Ref D8DF"}

    assert apply_agent._jobserve_identity_matches(auto_selected, intended) is True


def test_jobserve_auto_selected_identity_mismatch_requires_result_selection() -> None:
    intended = {"title": "Senior AI Engineer", "company_name": "Acme", "source_job_id": "D8DF"}
    auto_selected = {"title": "Data Analyst", "company": "Other", "reference": "ZZZ", "text": "Data Analyst Other Ref ZZZ"}
    candidates = [
        auto_selected,
        {"title": "Senior AI Engineer", "company": "Acme", "reference": "D8DF", "text": "Senior AI Engineer Acme Ref D8DF", "href": "/job/D8DF"},
    ]

    ranked = apply_agent._rank_jobserve_candidates(candidates, intended)

    assert apply_agent._jobserve_identity_matches(auto_selected, intended) is False
    assert ranked[0]["reference"] == "D8DF"


def test_jobserve_direct_job_url_is_preferred_over_search_url() -> None:
    assert apply_agent._jobserve_should_try_direct_url("https://www.jobserve.com/gb/en/job/ABC123") is True
    assert apply_agent._jobserve_should_try_direct_url("https://www.jobserve.com/FastTrack/Apply.aspx?jobid=ABC123") is True
    assert apply_agent._jobserve_should_try_direct_url("https://www.jobserve.com/gb/en/search-jobs-in-London,-London,-United-Kingdom/SC-CLEARED-AI-ML-ENGINEER-327CC8F570D6AB55A") is True
    assert apply_agent._jobserve_should_try_direct_url("https://www.jobserve.com/gb/en/Job-Search/") is False


def test_run_playwright_assist_default_visible_job_flag_is_false() -> None:
    assert apply_agent.run_playwright_assist.__kwdefaults__["use_current_selected_job_as_intended"] is False


def test_run_playwright_assist_passes_visible_job_flag(monkeypatch) -> None:
    captured: dict[str, bool] = {}

    class FakePage:
        def set_default_timeout(self, timeout):
            pass

        def set_default_navigation_timeout(self, timeout):
            pass

    class FakeBrowser:
        def new_page(self):
            return FakePage()

        def close(self):
            pass

    class FakeChromium:
        def launch(self, **kwargs):
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

    class FakeManager:
        def __enter__(self):
            return FakePlaywright()

        def __exit__(self, *args):
            return False

    def fake_search(page, browser, candidates, profile, job_context, **kwargs):
        captured["use_current_selected_job_as_intended"] = kwargs["use_current_selected_job_as_intended"]
        return AssistApplyResult(status="review_required", filled_fields=[], unfilled_fields=[], unfilled_required_fields=[], uploaded_cv=False, submitted=False, warnings=[], screenshot_path=None)

    monkeypatch.setattr(apply_agent, "validate_browser_automation_availability", lambda require_worker=False: SimpleNamespace(available=True, error=None, message=None))
    monkeypatch.setattr(apply_agent, "chromium_diagnostics", lambda: {"playwright_browsers_path": "", "chromium_executable_path": "", "chromium_file_exists": True, "chromium_file_executable": True})
    monkeypatch.setattr(apply_agent, "chromium_executable_path", lambda: None)
    monkeypatch.setitem(__import__("sys").modules, "playwright.sync_api", SimpleNamespace(Error=Exception, sync_playwright=lambda: FakeManager()))
    monkeypatch.setattr(apply_agent, "_run_jobserve_search_to_apply", fake_search)

    apply_agent.run_playwright_assist(
        "https://www.jobserve.com/gb/en/Job-Search/",
        {},
        apply_strategy="jobserve_apply_easy",
        use_current_selected_job_as_intended=True,
    )

    assert captured["use_current_selected_job_as_intended"] is True


def test_run_playwright_assist_reports_browser_launch_success(monkeypatch) -> None:
    steps: list[str] = []

    class FakePage:
        url = "about:blank"

        def set_default_timeout(self, timeout):
            pass

        def set_default_navigation_timeout(self, timeout):
            pass

        def goto(self, *args, **kwargs):
            self.url = args[0]

        def locator(self, selector):
            return SimpleNamespace(all=lambda: [])

    class FakeBrowser:
        contexts = []

        def new_page(self):
            return FakePage()

        def close(self):
            pass

    class FakeChromium:
        def launch(self, **kwargs):
            assert kwargs["timeout"] == 30000
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

    class FakeManager:
        def __enter__(self):
            return FakePlaywright()

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(apply_agent, "validate_browser_automation_availability", lambda require_worker=False: SimpleNamespace(available=True, error=None, message=None))
    monkeypatch.setattr(apply_agent, "chromium_diagnostics", lambda: {"playwright_browsers_path": "", "chromium_executable_path": "", "chromium_file_exists": True, "chromium_file_executable": True})
    monkeypatch.setattr(apply_agent, "chromium_executable_path", lambda: None)
    monkeypatch.setattr(apply_agent, "_captcha_visible", lambda page: False)
    monkeypatch.setattr(apply_agent, "_submit_visible", lambda page: False)
    monkeypatch.setitem(__import__("sys").modules, "playwright.sync_api", SimpleNamespace(Error=Exception, sync_playwright=lambda: FakeManager()))

    apply_agent.run_playwright_assist(
        "https://example.invalid/apply",
        {},
        progress_callback=lambda step, payload: steps.append(step),
    )

    assert "browser_launch_start" in steps
    assert "browser_launch_success" in steps
    assert "page_created" in steps
    assert "navigating_to_job_url" in steps


def test_jobserve_intended_result_missing_blocks() -> None:
    intended = {"title": "Senior AI Engineer", "company_name": "Acme", "source_job_id": "D8DF"}
    candidates = [{"title": "Data Analyst", "company": "Other", "reference": "ZZZ", "text": "Data Analyst Other Ref ZZZ"}]

    ranked = apply_agent._rank_jobserve_candidates(candidates, intended)

    assert ranked[0]["score"] == 0


def test_jobserve_modal_mismatch_blocks_submit() -> None:
    verified = {"title": "Senior AI Engineer", "reference": "D8DF"}
    modal = {"title": "Data Analyst", "reference": "ZZZ"}

    assert apply_agent._jobserve_identity_clear_mismatch(modal, verified) is True


def test_jobserve_submit_guard_allows_verified_ready_form() -> None:
    flow = {
        "intended_job_identity": {"title": "Senior AI Engineer", "company_name": "Acme", "source_job_id": "D8DF"},
        "email_filled": True,
        "confirmation_email_checked": True,
        "uk_status_selected": True,
    }
    verified = {"title": "Senior AI Engineer", "company": "Acme", "reference": "D8DF", "text": "Senior AI Engineer Acme Ref D8DF"}
    modal = {"title": "Senior AI Engineer", "reference": "D8DF"}
    page = _FakeEmailPage("alex@example.invalid")

    assert apply_agent._jobserve_submit_guard(flow, verified, modal, page, True, []) is None


def test_jobserve_submit_guard_blocks_mismatch() -> None:
    flow = {
        "intended_job_identity": {"title": "Senior AI Engineer", "company_name": "Acme", "source_job_id": "D8DF"},
        "email_filled": True,
        "confirmation_email_checked": True,
        "uk_status_selected": True,
    }
    verified = {"title": "Senior AI Engineer", "company": "Acme", "reference": "D8DF", "text": "Senior AI Engineer Acme Ref D8DF"}
    modal = {"title": "Data Analyst", "reference": "ZZZ"}
    page = _FakeEmailPage("alex@example.invalid")

    assert apply_agent._jobserve_submit_guard(flow, verified, modal, page, True, []) == "JobServe application modal does not match intended job"


def test_jobserve_submit_guard_blocks_missing_cv() -> None:
    flow = {
        "intended_job_identity": {"title": "Senior AI Engineer", "company_name": "Acme", "source_job_id": "D8DF"},
        "email_filled": True,
        "confirmation_email_checked": True,
        "uk_status_selected": True,
    }
    verified = {"title": "Senior AI Engineer", "company": "Acme", "reference": "D8DF", "text": "Senior AI Engineer Acme Ref D8DF"}
    modal = {"title": "Senior AI Engineer", "reference": "D8DF"}
    page = _FakeEmailPage("alex@example.invalid")

    assert apply_agent._jobserve_submit_guard(flow, verified, modal, page, False, []) == "CV is not attached"


def test_jobserve_production_missing_db_identity_blocks_submit() -> None:
    flow: dict = {}

    selected = apply_agent._verify_or_select_intended_jobserve_result(_FakeJobServeIdentityPage(), object(), {}, flow)

    assert selected is None
    assert flow["blocked_reason"] == "Intended JobServe job identity missing"


def test_jobserve_current_selected_identity_can_be_converted_to_intended_target() -> None:
    identity = {
        "title": "AI Engineer",
        "company": "Opus Recruitment Solutions Ltd",
        "reference": "ABC123",
        "href": "https://www.jobserve.com/job/ABC123",
        "location": "London",
        "salary": "GBP 600 per day",
    }

    target = apply_agent._jobserve_target_from_current_identity(identity)

    assert target["title"] == "AI Engineer"
    assert target["company_name"] == "Opus Recruitment Solutions Ltd"
    assert target["source_job_id"] == "ABC123"
    assert target["identity_source"] == "current_selected_job"
    assert apply_agent._jobserve_identity_matches(identity, target) is True


def test_jobserve_current_selected_detail_becomes_intended_identity() -> None:
    identity = {
        "title": "AI Engineer",
        "company": "Opus Recruitment Solutions Ltd",
        "reference": "ABC123",
        "href": "https://www.jobserve.com/job/ABC123",
        "text": "AI Engineer Opus Recruitment Solutions Ltd Reference ABC123",
    }
    flow: dict = {}

    target = apply_agent._jobserve_use_current_selected_job_as_intended(_FakeJobServeDetailPage(identity), flow)

    assert target["title"] == "AI Engineer"
    assert target["company_name"] == "Opus Recruitment Solutions Ltd"
    assert target["source_job_id"] == "ABC123"
    assert flow["identity_source"] == "current_selected_job"
    assert flow["auto_selected_matched"] is True


def test_jobserve_current_selected_missing_title_returns_specific_block_reason() -> None:
    flow: dict = {}

    target = apply_agent._jobserve_use_current_selected_job_as_intended(_FakeJobServeDetailPage({"text": "Apply now"}), flow)

    assert target["title"] == "Currently visible JobServe job"
    assert flow["local_debug_visible_job_warning"] == "LOCAL DEBUG ONLY: applying to currently visible JobServe job."


def test_jobserve_local_selected_identity_shortcut_does_not_change_production_missing_identity_block() -> None:
    production_flow: dict = {}
    selected = apply_agent._verify_or_select_intended_jobserve_result(_FakeJobServeIdentityPage(), object(), {}, production_flow)

    local_flow: dict = {}
    local_target = apply_agent._jobserve_use_current_selected_job_as_intended(
        _FakeJobServeDetailPage({"title": "AI Engineer", "company": "Acme", "reference": "ABC123", "href": "https://www.jobserve.com/gb/en/job/ABC123", "text": "AI Engineer Acme"}),
        local_flow,
    )

    assert selected is None
    assert production_flow["blocked_reason"] == "Intended JobServe job identity missing"
    assert local_target["source_job_id"] == "ABC123"
    assert local_flow["identity_source"] == "current_selected_job"


def test_jobserve_dropdown_helper_normalizes_visible_option_text() -> None:
    assert apply_agent._normalize_select_text("  Within   50 miles ") == apply_agent._normalize_select_text("within-50-miles")


def test_jobserve_dropdown_helper_falls_back_to_native_select() -> None:
    diagnostics: list[dict] = []
    with _playwright_page() as (page, _browser):
        page.set_content(
            """
            <label>Distance
              <select name="distance">
                <option>Within 10 miles</option>
                <option>Within 50 miles</option>
              </select>
            </label>
            """
        )

        selected = apply_agent.jobserve_click_dropdown_option(page, {"labels": [r"distance"]}, "Within 50 miles", field_name="Search distance", diagnostics=diagnostics)

        assert selected is True
        assert page.locator("select[name=distance]").input_value() == "Within 50 miles"
        assert diagnostics[-1]["success"] is True
        assert diagnostics[-1]["selected_option"] == "Within 50 miles"


def test_jobserve_dropdown_helper_already_selected_distance_passes() -> None:
    diagnostics: list[dict] = []
    with _playwright_page() as (page, _browser):
        page.set_content(
            """
            <label>Distance
              <select name="selRad">
                <option value="10">Within 10 miles</option>
                <option value="50" selected>Within 50 miles</option>
              </select>
            </label>
            """
        )

        selected = apply_agent.jobserve_click_dropdown_option(
            page,
            {"labels": [r"distance"], "selectors": ['select[name*="rad" i]']},
            "Within 50 miles",
            field_name="Search distance",
            diagnostics=diagnostics,
        )

        assert selected is True
        assert diagnostics[-1]["fallback_used"] == "already_selected"
        assert diagnostics[-1]["initial_selected_text"] == "Within 50 miles"
        assert diagnostics[-1]["final_selected_text"] == "Within 50 miles"
        assert diagnostics[-1]["detected_selects"][0]["name"] == "selRad"


def test_jobserve_dropdown_helper_clicks_visible_custom_option() -> None:
    diagnostics: list[dict] = []
    with _playwright_page() as (page, _browser):
        page.set_content(
            """
            <button id="distance" onclick="document.querySelector('#menu').style.display = 'block'">Distance</button>
            <div id="menu" style="display:none">
              <button onclick="window.selectedDistance = 'Within 50 miles'">Within 50 miles</button>
            </div>
            """
        )

        selected = apply_agent.jobserve_click_dropdown_option(page, page.locator("#distance"), "Within 50 miles", field_name="Search distance", diagnostics=diagnostics)

        assert selected is True
        assert page.evaluate("window.selectedDistance") == "Within 50 miles"
        assert diagnostics[-1]["dropdown_clicked"] is True
        assert diagnostics[-1]["fallback_used"] == "visible_text"


def test_jobserve_search_clicks_button_element() -> None:
    diagnostics: dict = {}
    with _playwright_page() as (page, _browser):
        page.set_content(
            """
            <button onclick="document.body.innerHTML = '<article data-jobid=&quot;1&quot;>AI Engineer</article>'">Search</button>
            """
        )

        assert apply_agent._click_jobserve_search(page, diagnostics) is True

        assert diagnostics["selector_used"] == "role_button_search"
        assert diagnostics["click_strategy"]
        assert diagnostics["results_wait"]["job_entries"] is True


def test_jobserve_search_clicks_input_submit() -> None:
    diagnostics: dict = {}
    with _playwright_page() as (page, _browser):
        page.set_content(
            """
            <input type="submit" value="Search" onclick="document.body.innerHTML = '<div class=&quot;job-result&quot;>AI Engineer</div>'" />
            """
        )

        assert apply_agent._click_jobserve_search(page, diagnostics) is True

        assert diagnostics["selector_used"] in {"input_submit_value_search", "role_button_search"}
        assert diagnostics["input_submit_buttons"][0]["value"] == "Search"


def test_jobserve_search_enter_key_fallback() -> None:
    diagnostics: dict = {}
    with _playwright_page() as (page, _browser):
        page.set_content(
            """
            <input name="keywords" onkeydown="if (event.key === 'Enter') document.body.innerHTML = '<article data-jobid=&quot;2&quot;>AI Engineer</article>'" />
            """
        )

        assert apply_agent._click_jobserve_search(page, diagnostics) is True

        assert diagnostics["selector_used"] == "enter_key_fallback"
        assert diagnostics["results_wait"]["job_entries"] is True


def test_jobserve_results_wait_detection() -> None:
    diagnostics: dict = {}
    with _playwright_page() as (page, _browser):
        page.set_content("<div>42 jobs found</div><div class='job-result'>AI Engineer</div>")

        assert apply_agent._wait_for_jobserve_results(page, page.url, diagnostics) is True

        assert diagnostics["result_count_text"] is True
        assert diagnostics["job_entries"] is True


def test_jobserve_search_click_failure_returns_diagnostic() -> None:
    diagnostics: dict = {}
    with _playwright_page() as (page, _browser):
        page.set_content("<div>No search control</div>")

        assert apply_agent._click_jobserve_search(page, diagnostics) is False

        assert diagnostics["failure_reason"]
        assert "visible_buttons" in diagnostics
        assert "input_submit_buttons" in diagnostics
        assert "search_links" in diagnostics


def test_jobserve_results_page_first_job_apply_button_detection() -> None:
    with _playwright_page() as (page, browser):
        page.set_content(
            """
            <div class="job-result selected">AI Engineer</div>
            <section id="detail"><button>Apply</button></section>
            """
        )

        target = apply_agent._find_apply_target(page, browser)

        assert target is not None
        assert target.inner_text() == "Apply"


def test_jobserve_confirmation_checkbox_already_ticked_passes() -> None:
    flow: dict = {}
    with _playwright_page() as (page, _browser):
        page.set_content(
            """
            <label>Send confirmation of my application to this Email Address
              <input type="checkbox" checked onclick="window.clicked = true" />
            </label>
            """
        )

        apply_agent._ensure_confirmation_email_checked(page, flow)

        assert flow["confirmation_email_checked"] is True
        assert flow["confirmation_checkbox_diagnostic"]["result"] == "already_checked"
        assert page.evaluate("window.clicked") is None


def test_jobserve_confirmation_checkbox_unticked_gets_ticked() -> None:
    flow: dict = {}
    with _playwright_page() as (page, _browser):
        page.set_content(
            """
            <label>Send confirmation of my application to this Email Address
              <input type="checkbox" onclick="window.clicked = true" />
            </label>
            """
        )

        apply_agent._ensure_confirmation_email_checked(page, flow)

        assert flow["confirmation_email_checked"] is True
        assert flow["confirmation_checkbox_diagnostic"]["result"] == "checked_after_click"
        assert page.evaluate("window.clicked") is True


def test_jobserve_working_status_selects_uk_citizen() -> None:
    diagnostics: list[dict] = []
    with _playwright_page() as (page, _browser):
        page.set_content(
            """
            <label>Working status in UK
              <select name="status"><option></option><option>UK Citizen</option></select>
            </label>
            """
        )

        assert apply_agent._select_work_status(page, "UK Citizen", diagnostics) is True

        assert page.locator("select[name=status]").input_value() == "UK Citizen"


def test_jobserve_filcv_upload_detection() -> None:
    with _playwright_page() as (page, _browser):
        page.set_content('<input id="filCV" type="file" />')

        assert apply_agent._jobserve_cv_file_input(page).count() == 1


def test_jobserve_submit_success_message_detection() -> None:
    with _playwright_page() as (page, browser):
        page.set_content("<div>Your application has been submitted.</div>")

        apply_agent._wait_for_jobserve_submission_success(page, browser)


def test_jobserve_submit_success_message_detection_from_body_text() -> None:
    with _playwright_page() as (page, browser):
        page.set_content("<main><p>Thank you for your application.</p></main>")

        assert "Thank you" in apply_agent._wait_for_jobserve_submission_success(page, browser)


def test_jobserve_post_submit_state_infers_acceptance_when_form_disappears() -> None:
    with _playwright_page() as (page, browser):
        page.set_content("<main><p>Your details have been sent to the advertiser.</p></main>")

        state = apply_agent._jobserve_post_submit_state(page, browser)

        assert state["submission_likely_accepted"] is True
        assert state["visible_submit_count"] == 0


def test_jobserve_post_submit_state_blocks_validation_error() -> None:
    with _playwright_page() as (page, browser):
        page.set_content('<form><p>Please upload your CV.</p><input type="file"><input type="submit" value="Apply"></form>')

        state = apply_agent._jobserve_post_submit_state(page, browser)

        assert state["submission_likely_accepted"] is False
        assert state["failure_text"]


def test_jobserve_registration_toggle_unknown_defaults_clicked_off() -> None:
    warnings: list[str] = []
    with _playwright_page() as (page, _browser):
        page.set_content(
            """
            <div role="checkbox" aria-label="I would like to register a Job Seeker account"
                 onclick="window.clicked = true"></div>
            """
        )

        disabled = apply_agent._disable_jobserve_account_options(page, warnings)

        assert "I would like to register a Job Seeker account" in disabled
        assert page.evaluate("window.clicked") is True


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


def test_jobserve_candidates_default_required_dropdown_values() -> None:
    user = User(email="user@example.invalid")
    profile = SimpleNamespace(preferences={}, work_status_uk="SC Cleared; BPSS Cleared")

    candidates = apply_agent.profile_field_candidates(user, profile)

    assert candidates["availability_notice"].value == "Immediate"
    assert candidates["salary_expectation_gbp"].value == "65000"
    assert candidates["travel_distance_miles"].value == "30"
    assert candidates["work_authorization"].value == "UK Citizen"


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


def test_generated_jobserve_dropdowns_are_selected_by_option_sets() -> None:
    warnings: list[str] = []
    filled: list[str] = []
    unfilled_required: list[str] = []
    diagnostics: list[dict] = []
    page = _FakeSelectPage(["Immediate", "1 Week", "2 Weeks", "3 Weeks"])

    apply_agent._handle_required_dropdown(
        page,
        {"availability_notice": apply_agent.FieldCandidate("availability_notice", "Immediate", "fixture")},
        "availability_notice",
        [r"availability"],
        lambda value: value,
        "Availability notice",
        "Availability notice missing",
        filled,
        unfilled_required,
        warnings,
        diagnostics,
    )

    assert filled == ["Availability notice"]
    assert unfilled_required == []
    assert page.selected == "Immediate"


def test_salary_and_travel_generated_dropdowns_choose_matching_ranges() -> None:
    hourly_page = _FakeSelectPage(["0 - £10 Per Hour", "£10 - £20 Per Hour", "£20 - £30 Per Hour", "£30 - £40 Per Hour", "£40 - £50 Per Hour", ">£100"])
    travel_page = _FakeSelectPage(["Up to 5", "Up to 15", "Up to 30", "Up to 50", "50+"])

    assert apply_agent._select_required_dropdown_by_options(hourly_page, "salary_expectation_gbp", "65000", "£50,000 - £75,000", field_name="Salary expectation")
    assert apply_agent._select_required_dropdown_by_options(travel_page, "travel_distance_miles", "25", "16 to 30", field_name="Travel distance")
    assert hourly_page.selected == "£30 - £40 Per Hour"
    assert travel_page.selected == "Up to 30"


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
        diagnostics: list[dict] = []
        flow = {"search_defaults": apply_agent._jobserve_search_preferences(SimpleNamespace(preferences={})), "search_controls": {}}

        assert apply_agent._fill_jobserve_search_form(page, flow, diagnostics) is True

        assert page.locator("input[name=keywords]").input_value() == "AI"
        assert page.locator("input[name=location]").input_value() == "London"
        assert page.get_by_label("Remote only").is_checked() is False
        assert page.evaluate("window.selectedAll") is True
        assert {"Search distance", "Posted within", "Job type", "Industries"}.issubset({item["field"] for item in diagnostics})


def test_jobserve_remote_checkbox_already_unchecked_succeeds_without_click() -> None:
    diagnostic: dict = {}
    with _playwright_page() as (page, _browser):
        page.set_content(
            """
            <label>Only show jobs with remote working
              <input type="checkbox" name="remote" onclick="window.clicked = true" />
            </label>
            """
        )

        result = apply_agent._set_checkbox_by_label(page, [r"only show jobs with remote working"], checked=False, diagnostic=diagnostic)

        assert result is True
        assert page.evaluate("window.clicked") is None
        assert diagnostic["checkbox_found"] is True
        assert diagnostic["initial_checked"] is False
        assert diagnostic["clicked"] is False
        assert diagnostic["final_checked"] is False
        assert diagnostic["result"] == "already_unchecked"


def test_jobserve_remote_checkbox_checked_clicks_and_succeeds() -> None:
    diagnostic: dict = {}
    with _playwright_page() as (page, _browser):
        page.set_content(
            """
            <label>Only show jobs with remote working
              <input type="checkbox" name="remote" checked onclick="window.clicked = true" />
            </label>
            """
        )

        result = apply_agent._set_checkbox_by_label(page, [r"only show jobs with remote working"], checked=False, diagnostic=diagnostic)

        assert result is True
        assert page.evaluate("window.clicked") is True
        assert page.locator("input[name=remote]").is_checked() is False
        assert diagnostic["initial_checked"] is True
        assert diagnostic["clicked"] is True
        assert diagnostic["final_checked"] is False
        assert diagnostic["result"] == "unchecked_after_click"


def test_jobserve_remote_checkbox_not_found_is_not_success() -> None:
    diagnostic: dict = {}
    with _playwright_page() as (page, _browser):
        page.set_content("<div>No remote filter here</div>")

        result = apply_agent._set_checkbox_by_label(page, [r"only show jobs with remote working"], checked=False, diagnostic=diagnostic)

        assert result is False
        assert diagnostic["checkbox_found"] is False
        assert diagnostic["result"] == "not_found"


def test_jobserve_remote_checkbox_styled_fallback_clicks_box() -> None:
    diagnostic: dict = {}
    with _playwright_page() as (page, _browser):
        page.set_content(
            """
            <div role="checkbox" aria-checked="true" aria-label="Only show jobs with remote working"
                 onclick="this.setAttribute('aria-checked', this.getAttribute('aria-checked') === 'true' ? 'false' : 'true'); window.clicked = true">
            </div>
            """
        )

        result = apply_agent._set_checkbox_by_label(page, [r"only show jobs with remote working"], checked=False, diagnostic=diagnostic)

        assert result is True
        assert page.evaluate("window.clicked") is True
        assert diagnostic["initial_checked"] is True
        assert diagnostic["clicked"] is True
        assert diagnostic["final_checked"] is False
        assert diagnostic["result"] == "unchecked_after_click"


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
        steps: list[str] = []

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
            step_callback=lambda step, payload: steps.append(step),
        )

        assert result["uploaded_cv"] is True
        assert page.locator("input[name=email]").input_value() == "alex@example.invalid"
        assert page.get_by_label("Send confirmation of my application to this Email Address").is_checked() is True
        assert page.evaluate("window.submitted") is None
        assert "CV upload" in filled
        assert "email_filled" in steps
        assert "confirmation_checked" in steps
        assert "working_status_selected" in steps
        assert "cv_upload_started" in steps
        assert "cv_uploaded" in steps


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


def _seed_application(db_session, *, url: str | None = None, jobserve: bool = False) -> tuple[User, Job]:
    if url is None:
        url = "https://www.jobserve.com/gb/en/job/D8DF" if jobserve else "https://example.invalid/apply"
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


class _FakeJobServeIdentityPage:
    def evaluate(self, *args, **kwargs):
        return []


class _FakeJobServeDetailPage:
    def __init__(self, identity: dict) -> None:
        self.identity = identity

    def evaluate(self, script, *args, **kwargs):
        if "querySelectorAll('a[href], article" in str(script):
            return []
        return self.identity


class _FakeEmailPage:
    def __init__(self, value: str) -> None:
        self.value = value

    def get_by_label(self, pattern):
        return self

    @property
    def first(self):
        return self

    def input_value(self, timeout=500):
        return self.value


class _FakeSelectPage:
    def __init__(self, options: list[str] | None = None) -> None:
        self.selected = None
        self.options = options or ["Immediate", "One month"]

    def get_by_label(self, pattern):
        return _FakeSelectLocator(self)

    def locator(self, selector):
        assert selector == "select"
        return _FakeSelectCollection(self)


class _FakeSelectCollection:
    def __init__(self, page: _FakeSelectPage) -> None:
        self.page = page

    def all(self):
        return [_FakeSelectLocator(self.page)]


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


def test_jobserve_already_applied_confirmation_counts_as_submitted() -> None:
    assert apply_agent._jobserve_confirmation_means_submitted("You have already applied for this job") is True
    assert apply_agent._jobserve_confirmation_means_submitted("Your application has been submitted.") is True
    assert apply_agent._jobserve_confirmation_means_submitted("Validation error") is False
