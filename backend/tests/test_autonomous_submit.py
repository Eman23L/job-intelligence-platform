from __future__ import annotations

import asyncio

import pytest

from app.config import settings
from app.db.models import Job, User
from app.schemas.database import AssistApplyResult
from app.services import apply_agent
from app.services.apply_agent import BrowserAutomationError
from app.services import autonomous_submit
from sqlalchemy.orm.attributes import flag_modified


def _safe_diag(reference: str = "ABC123", *, cv_found: bool = True) -> dict:
    return {
        "run_id": "safe-1",
        "status": "passed",
        "safe_mode": True,
        "submit_allowed": False,
        "overall_status": "ok",
        "source_job_id": reference,
        "job_title": "AI Engineer",
        "job_company": "Acme",
        "cv_found": cv_found,
    }


def _job(**kwargs) -> Job:
    defaults = {
        "source_id": 1,
        "source_job_id": "ABC123",
        "canonical_url": "https://www.jobserve.com/gb/en/job/ABC123",
        "title": "AI Engineer",
        "company_name": "Acme",
        "application_status": "ready_to_apply",
        "assisted_result": {"latest_safe_diagnostic": _safe_diag()},
    }
    defaults.update(kwargs)
    return Job(**defaults)


def test_autonomous_submit_disabled_by_default(monkeypatch) -> None:
    monkeypatch.setattr(settings, "autonomous_real_submit_enabled", False)

    report = autonomous_submit.autonomous_real_submit_verification(_job())

    assert report["overall_status"] == "failed"
    assert report["failed_phase"] == "feature_enabled"


def test_only_safe_diagnostic_passed_applications_are_eligible(monkeypatch) -> None:
    monkeypatch.setattr(settings, "autonomous_real_submit_enabled", True)
    job = _job(assisted_result={"latest_safe_diagnostic": {**_safe_diag(), "status": "failed"}})

    assert autonomous_submit.latest_safe_diagnostic_passed(job) is False
    assert autonomous_submit.autonomous_real_submit_verification(job)["failed_phase"] == "latest_safe_diagnostic_passed"


def test_exact_job_id_mismatch_blocks_submit(monkeypatch) -> None:
    monkeypatch.setattr(settings, "autonomous_real_submit_enabled", True)
    job = _job(assisted_result={"latest_safe_diagnostic": _safe_diag("WRONG")})

    report = autonomous_submit.autonomous_real_submit_verification(job)

    assert report["failed_phase"] == "exact_job_reference_matches"


def test_missing_cv_blocks_submit(monkeypatch) -> None:
    monkeypatch.setattr(settings, "autonomous_real_submit_enabled", True)
    job = _job(assisted_result={"latest_safe_diagnostic": _safe_diag(cv_found=False)})

    report = autonomous_submit.autonomous_real_submit_verification(job)

    assert report["failed_phase"] == "cv_available"


def test_no_duplicate_submit(monkeypatch) -> None:
    monkeypatch.setattr(settings, "autonomous_real_submit_enabled", True)
    job = _job(assisted_result={"latest_safe_diagnostic": _safe_diag(), "autonomous_real_submit_attempted": True})

    report = autonomous_submit.autonomous_real_submit_verification(job)

    assert report["failed_phase"] == "not_previously_autonomous_attempted"


def test_success_text_required_before_marking_submitted(db_session, monkeypatch) -> None:
    monkeypatch.setattr(settings, "autonomous_real_submit_enabled", True)
    user = User(email="user@example.com")
    job = _job()
    db_session.add_all([user, job])
    db_session.commit()

    monkeypatch.setattr(
        autonomous_submit,
        "assist_apply_application",
        lambda *args, **kwargs: AssistApplyResult(status="submitted", submitted=True, confirmation_text=None),
    )
    diagnostics = []
    monkeypatch.setattr(autonomous_submit, "run_assist_apply_probe_background", lambda *args, **kwargs: diagnostics.append(args))

    result = autonomous_submit.run_autonomous_real_submit_canary(db_session, user)

    assert result["status"] == "failed"
    assert result["failed_phase"] == "success_text_missing"
    assert job.application_status != "applied"
    assert diagnostics


def test_first_failure_stops_run(db_session, monkeypatch) -> None:
    monkeypatch.setattr(settings, "autonomous_real_submit_enabled", True)
    monkeypatch.setattr(settings, "max_autonomous_real_submits_per_run", 1)
    user = User(email="user@example.com")
    first = _job(source_job_id="ABC123", canonical_url="https://www.jobserve.com/gb/en/job/ABC123", assisted_result={"latest_safe_diagnostic": _safe_diag("ABC123")})
    second = _job(source_job_id="DEF456", canonical_url="https://www.jobserve.com/gb/en/job/DEF456", assisted_result={"latest_safe_diagnostic": _safe_diag("DEF456")})
    db_session.add_all([user, first, second])
    db_session.commit()

    attempts = []

    def fail_once(*args, **kwargs):
        attempts.append(args)
        raise RuntimeError("submit failed")

    monkeypatch.setattr(autonomous_submit, "assist_apply_application", fail_once)
    monkeypatch.setattr(autonomous_submit, "run_assist_apply_probe_background", lambda *args, **kwargs: None)

    result = autonomous_submit.run_autonomous_real_submit_canary(db_session, user)

    assert result["status"] == "failed"
    assert len(attempts) == 1
    assert result["application_id"] == first.id
    assert result["attempt_number"] == 1
    assert result["will_retry_same_application"] is True
    assert result["will_move_to_next_application"] is False


def test_validation_errors_block_submit(monkeypatch) -> None:
    monkeypatch.setattr(settings, "autonomous_real_submit_enabled", True)
    job = _job(application_status="failed")

    report = autonomous_submit.autonomous_real_submit_verification(job)

    assert report["failed_phase"] == "application_status_allowed"


def test_canary_no_eligible_failed_app_triggers_diagnostics(db_session, monkeypatch) -> None:
    monkeypatch.setattr(settings, "autonomous_real_submit_enabled", True)
    user = User(email="user@example.com")
    job = _job(application_status="failed", assisted_result={"final_error": "worker_progress_timeout"})
    db_session.add_all([user, job])
    db_session.commit()

    diagnostics = []

    def pass_diagnostic(run_id, application_id, user_id, safe_mode=True, submit_allowed=False):
        diagnostics.append((run_id, application_id, safe_mode, submit_allowed))
        job.assisted_result = {
            **(job.assisted_result or {}),
            "latest_safe_diagnostic": _safe_diag(),
        }
        flag_modified(job, "assisted_result")
        db_session.commit()

    monkeypatch.setattr(autonomous_submit, "run_assist_apply_probe_background", pass_diagnostic)
    monkeypatch.setattr(autonomous_submit, "assist_apply_application", lambda *args, **kwargs: AssistApplyResult(status="submitted", submitted=True, confirmation_text="Your application has been submitted."))

    result = autonomous_submit.run_autonomous_real_submit_canary(db_session, user)

    assert diagnostics
    assert diagnostics[0][2] is True
    assert diagnostics[0][3] is False
    assert result["submitted"] is True
    assert result["orchestration_steps"][0]["reset_performed"] is True
    assert result["orchestration_steps"][0]["retried"] is True


def test_diagnostic_fail_creates_codex_handoff(db_session, monkeypatch) -> None:
    monkeypatch.setattr(settings, "autonomous_real_submit_enabled", True)
    monkeypatch.setattr(
        autonomous_submit,
        "create_or_update_codex_handoff",
        lambda report: {"status": "created", "issue_url": "https://github.com/owner/repo/issues/10"},
    )
    user = User(email="user@example.com")
    job = _job(application_status="failed", assisted_result={"final_error": "stale_queue_timeout"})
    db_session.add_all([user, job])
    db_session.commit()

    def fail_diagnostic(*args, **kwargs):
        job.assisted_result = {
            **(job.assisted_result or {}),
            "latest_safe_diagnostic": {
                "status": "failed",
                "overall_status": "failed",
                "safe_mode": True,
                "submit_allowed": False,
                "failed_phase": "jobserve_navigation",
                "exact_error": "modal missing",
                "recommended_fix": "Fix selector.",
                "artifact_links": ["report.json"],
            },
        }
        flag_modified(job, "assisted_result")
        db_session.commit()

    monkeypatch.setattr(autonomous_submit, "run_assist_apply_probe_background", fail_diagnostic)

    result = autonomous_submit.run_autonomous_real_submit_canary(db_session, user)

    assert result["status"] == "failed"
    assert result["failed_phase"] == "jobserve_navigation"
    assert result["codex_handoff_status"] == "created"
    assert result["github_issue_url"].endswith("/10")
    assert result["orchestration_steps"][0]["codex_handoff_created"] is True


def test_safe_diagnostic_missing_triggers_diagnostic_automatically(db_session, monkeypatch) -> None:
    monkeypatch.setattr(settings, "autonomous_real_submit_enabled", False)
    user = User(email="user@example.com")
    job = _job(assisted_result={})
    db_session.add_all([user, job])
    db_session.commit()
    called = []
    monkeypatch.setattr(autonomous_submit, "run_assist_apply_probe_background", lambda *args, **kwargs: called.append(args))

    autonomous_submit.run_autonomous_real_submit_canary(db_session, user)

    assert called


def test_no_infinite_loop_when_reset_still_not_eligible(db_session, monkeypatch) -> None:
    monkeypatch.setattr(settings, "autonomous_real_submit_enabled", True)
    user = User(email="user@example.com")
    job = _job(application_status="failed", assisted_result={"final_error": "timeout"})
    db_session.add_all([user, job])
    db_session.commit()

    def mismatch_diagnostic(*args, **kwargs):
        job.assisted_result = {**(job.assisted_result or {}), "latest_safe_diagnostic": _safe_diag("WRONG")}
        flag_modified(job, "assisted_result")
        db_session.commit()

    monkeypatch.setattr(autonomous_submit, "run_assist_apply_probe_background", mismatch_diagnostic)
    monkeypatch.setattr(
        autonomous_submit,
        "create_or_update_codex_handoff",
        lambda report: {"status": "created", "issue_url": "https://github.com/owner/repo/issues/11"},
    )

    result = autonomous_submit.run_autonomous_real_submit_canary(db_session, user)

    assert len(result["orchestration_steps"]) == 1
    assert result["orchestration_steps"][0]["retried"] is True
    assert result["orchestration_steps"][0]["final_outcome"] == "blocked_after_recovery"
    assert result["codex_handoff_status"] == "created"


def test_canary_failure_creates_codex_handoff_issue(db_session, monkeypatch, caplog) -> None:
    monkeypatch.setattr(settings, "autonomous_real_submit_enabled", True)
    user = User(email="user@example.com")
    job = _job(
        assisted_result={
            "latest_safe_diagnostic": _safe_diag(),
            "progress": {"current_step": "modal_wait_start"},
            "running_step": "modal_wait_start",
            "debug_steps": [{"step": "modal_wait_start", "current_url": "https://www.jobserve.com/job/ABC123", "page_title": "JobServe"}],
            "screenshot_paths": ["modal.jpg"],
            "html_snapshot_paths": ["modal.html"],
        }
    )
    db_session.add_all([user, job])
    db_session.commit()
    handoff_reports = []
    monkeypatch.setattr(autonomous_submit, "run_assist_apply_probe_background", lambda *args, **kwargs: None)
    monkeypatch.setattr(autonomous_submit, "assist_apply_application", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("submit failed")))
    monkeypatch.setattr(
        autonomous_submit,
        "create_or_update_codex_handoff",
        lambda report: handoff_reports.append(report) or {"status": "created", "issue_url": "https://github.com/owner/repo/issues/12"},
    )

    with caplog.at_level("ERROR"):
        result = autonomous_submit.run_autonomous_real_submit_canary(db_session, user)

    assert result["status"] == "failed"
    assert result["failed_phase"] == "modal_wait"
    assert result["exact_error"] == "RuntimeError: submit failed"
    assert result["traceback"]
    assert "RuntimeError: submit failed" in result["traceback"]
    assert result["last_known_stage"] == "modal_wait_start"
    assert result["current_url"] == "https://www.jobserve.com/job/ABC123"
    assert result["artifact_links"] == ["modal.jpg", "modal.html"]
    assert result["codex_handoff_status"] == "created"
    assert result["github_issue_url"].endswith("/12")
    assert "autonomous_submit_failed" in caplog.text
    assert "application_id=" in caplog.text
    assert handoff_reports[0]["exact_error"] == "RuntimeError: submit failed"
    assert "RuntimeError: submit failed" in handoff_reports[0]["traceback"]
    assert handoff_reports[0]["last_known_stage"] == "modal_wait_start"


def test_canary_stage_failure_creates_phase_specific_handoff(db_session, monkeypatch) -> None:
    monkeypatch.setattr(settings, "autonomous_real_submit_enabled", True)
    user = User(email="user@example.com")
    job = _job()
    db_session.add_all([user, job])
    db_session.commit()
    handoff_reports = []
    monkeypatch.setattr(autonomous_submit, "run_assist_apply_probe_background", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        autonomous_submit,
        "assist_apply_application",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            BrowserAutomationError(
                "jobserve_navigation",
                "navigation failed",
                {
                    "current_url": "https://www.jobserve.com/job/ABC123",
                    "page_title": "JobServe",
                    "screenshot_paths": ["before.jpg"],
                    "html_snapshot_paths": ["before.html"],
                    "detected_buttons": [{"text": "Apply"}],
                    "detected_fields": [{"name": "email"}],
                    "traceback": "Traceback...",
                },
            )
        ),
    )
    monkeypatch.setattr(
        autonomous_submit,
        "create_or_update_codex_handoff",
        lambda report: handoff_reports.append(report) or {"status": "updated", "issue_url": "https://github.com/owner/repo/issues/16"},
    )

    result = autonomous_submit.run_autonomous_real_submit_canary(db_session, user)

    assert result["failed_phase"] == "jobserve_navigation"
    assert result["exact_error"] == "navigation failed"
    assert result["current_url"] == "https://www.jobserve.com/job/ABC123"
    assert result["page_title"] == "JobServe"
    assert result["screenshot_paths"] == ["before.jpg"]
    assert result["html_snapshot_paths"] == ["before.html"]
    assert result["artifact_links"] == ["before.jpg", "before.html"]
    assert result["detected_buttons"] == [{"text": "Apply"}]
    assert result["detected_fields"] == [{"name": "email"}]
    assert handoff_reports[0]["failed_phase"] == "jobserve_navigation"
    assert handoff_reports[0]["exact_error"] == "navigation failed"


@pytest.mark.parametrize("phase", ["modal_wait", "cv_upload", "final_submit_click"])
def test_canary_known_stage_failures_are_not_generic(db_session, monkeypatch, phase) -> None:
    user = User(email=f"{phase}@example.com")
    job = _job(source_job_id=phase, canonical_url=f"https://www.jobserve.com/gb/en/job/{phase}", assisted_result={"latest_safe_diagnostic": _safe_diag(phase)})
    db_session.add_all([user, job])
    db_session.commit()
    monkeypatch.setattr(settings, "autonomous_real_submit_enabled", True)
    monkeypatch.setattr(autonomous_submit, "run_assist_apply_probe_background", lambda *args, **kwargs: None)
    monkeypatch.setattr(autonomous_submit, "create_or_update_codex_handoff", lambda report: {"status": "updated", "issue_url": "https://github.com/owner/repo/issues/17"})
    monkeypatch.setattr(
        autonomous_submit,
        "assist_apply_application",
        lambda *args, **kwargs: (_ for _ in ()).throw(BrowserAutomationError(phase, f"{phase} failed", {"traceback": "Traceback..."})),
    )

    result = autonomous_submit.run_autonomous_real_submit_canary(db_session, user)

    assert result["failed_phase"] == phase
    assert result["exact_error"] == f"{phase} failed"
    assert result["failed_phase"] != "autonomous_submit_exception"


def test_missing_chromium_creates_browser_launch_handoff(db_session, monkeypatch) -> None:
    monkeypatch.setattr(settings, "autonomous_real_submit_enabled", True)
    user = User(email="user@example.com")
    job = _job()
    db_session.add_all([user, job])
    db_session.commit()
    monkeypatch.setattr(autonomous_submit, "run_assist_apply_probe_background", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        autonomous_submit,
        "assist_apply_application",
        lambda *args, **kwargs: (_ for _ in ()).throw(BrowserAutomationError("chromium_not_installed", "Executable doesn't exist at /opt/render/.cache/ms-playwright/chromium-1140/chrome-linux/chrome")),
    )
    monkeypatch.setattr(
        autonomous_submit,
        "create_or_update_codex_handoff",
        lambda report: {"status": "created", "issue_url": "https://github.com/owner/repo/issues/14", "attempt_count": 1},
    )

    result = autonomous_submit.run_autonomous_real_submit_canary(db_session, user)

    assert result["status"] == "failed"
    assert result["failed_phase"] == "browser_launch"
    assert result["codex_handoff_status"] == "created"
    assert result["codex_handoff_attempt_count"] == 1


def test_canary_logs_browser_preflight_before_submit(db_session, monkeypatch, caplog) -> None:
    monkeypatch.setattr(settings, "autonomous_real_submit_enabled", True)
    user = User(email="user@example.com")
    job = _job()
    db_session.add_all([user, job])
    db_session.commit()
    monkeypatch.setattr(
        autonomous_submit,
        "chromium_diagnostics",
        lambda: {
            "playwright_browsers_path": "0",
            "chromium_executable_path": "/app/.local-browsers/chromium/chrome",
            "chromium_file_exists": True,
            "chromium_file_executable": True,
        },
    )
    monkeypatch.setattr(autonomous_submit, "assist_apply_application", lambda *args, **kwargs: AssistApplyResult(status="submitted", submitted=True, confirmation_text="Your application has been submitted."))

    with caplog.at_level("INFO"):
        autonomous_submit.run_autonomous_real_submit_canary(db_session, user)

    assert "autonomous_submit_browser_preflight" in caplog.text
    assert "chromium_executable_path=/app/.local-browsers/chromium/chrome" in caplog.text
    assert "chromium_file_exists=True" in caplog.text


def test_async_autonomous_canary_preflight_does_not_call_sync_playwright(db_session, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "autonomous_real_submit_enabled", True)
    user = User(email="user@example.com")
    job = _job()
    db_session.add_all([user, job])
    db_session.commit()

    local_browsers = tmp_path / "driver" / "package" / ".local-browsers"
    executable = local_browsers / "chromium-1201" / "chrome-linux" / "chrome"
    executable.parent.mkdir(parents=True)
    executable.write_text("", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "0")
    monkeypatch.setattr(autonomous_submit, "playwright_installed", lambda: True, raising=False)
    monkeypatch.setattr("app.services.browser_automation.playwright_installed", lambda: True)
    monkeypatch.setattr("app.services.browser_automation._hermetic_browser_root", lambda: local_browsers)
    monkeypatch.setattr(
        "app.services.browser_automation._playwright_chromium_executable_path",
        lambda: (_ for _ in ()).throw(AssertionError("sync_playwright should not be called in an event loop")),
    )
    monkeypatch.setattr(autonomous_submit, "assist_apply_application", lambda *args, **kwargs: AssistApplyResult(status="submitted", submitted=True, confirmation_text="Your application has been submitted."))

    async def run_canary():
        return autonomous_submit.run_autonomous_real_submit_canary(db_session, user)

    result = asyncio.run(run_canary())

    assert result["status"] == "submitted"


def test_playwright_sync_async_mismatch_classified_and_handed_off(db_session, monkeypatch) -> None:
    monkeypatch.setattr(settings, "autonomous_real_submit_enabled", True)
    user = User(email="user@example.com")
    job = _job()
    db_session.add_all([user, job])
    db_session.commit()
    handoff_reports = []
    monkeypatch.setattr(autonomous_submit, "run_assist_apply_probe_background", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        autonomous_submit,
        "assist_apply_application",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("It looks like you are using Playwright Sync API inside the asyncio loop. Please use the Async API instead.")),
    )
    monkeypatch.setattr(
        autonomous_submit,
        "create_or_update_codex_handoff",
        lambda report: handoff_reports.append(report) or {"status": "updated", "issue_url": "https://github.com/owner/repo/issues/15"},
    )

    result = autonomous_submit.run_autonomous_real_submit_canary(db_session, user)

    assert result["status"] == "failed"
    assert result["failed_phase"] == "playwright_api_mismatch"
    assert result["exact_error"] == "Sync Playwright API called inside asyncio loop"
    assert result["recommended_fix"] == "Use async_playwright or move sync check outside async runtime."
    assert result["codex_handoff_status"] == "updated"
    assert handoff_reports[0]["failed_phase"] == "playwright_api_mismatch"
    assert handoff_reports[0]["exact_error"] == "Sync Playwright API called inside asyncio loop"


def test_run_playwright_assist_rejects_sync_api_inside_asyncio_loop(monkeypatch) -> None:
    monkeypatch.setattr(apply_agent, "validate_browser_automation_availability", lambda require_worker=False: type("Availability", (), {"available": True, "error": None, "message": None})())
    monkeypatch.setattr(apply_agent, "chromium_diagnostics", lambda: {"playwright_browsers_path": "0", "chromium_executable_path": "/chromium", "chromium_file_exists": True, "chromium_file_executable": True})
    monkeypatch.setitem(
        __import__("sys").modules,
        "playwright.sync_api",
        type("SyncApi", (), {"Error": Exception, "sync_playwright": lambda: (_ for _ in ()).throw(AssertionError("sync_playwright should not be called"))})(),
    )

    async def run_assist():
        return apply_agent.run_playwright_assist("https://example.invalid", {}, apply_strategy="jobserve_apply_easy")

    try:
        asyncio.run(run_assist())
    except BrowserAutomationError as exc:
        assert exc.error == "playwright_api_mismatch"
        assert exc.message == "Sync Playwright API called inside asyncio loop"
    else:
        raise AssertionError("Expected BrowserAutomationError")


def test_second_failure_stays_on_same_application(db_session, monkeypatch) -> None:
    monkeypatch.setattr(settings, "autonomous_real_submit_enabled", True)
    user = User(email="user@example.com")
    first = _job(assisted_result={"latest_safe_diagnostic": _safe_diag(), "autonomous_fix_attempt_count": 1})
    second = _job(source_job_id="DEF456", canonical_url="https://www.jobserve.com/gb/en/job/DEF456", assisted_result={"latest_safe_diagnostic": _safe_diag("DEF456")})
    db_session.add_all([user, first, second])
    db_session.commit()
    attempts = []
    monkeypatch.setattr(autonomous_submit, "assist_apply_application", lambda db, job, user, **kwargs: attempts.append(job.id) or (_ for _ in ()).throw(RuntimeError("still failing")))
    monkeypatch.setattr(autonomous_submit, "run_assist_apply_probe_background", lambda *args, **kwargs: None)

    result = autonomous_submit.run_autonomous_real_submit_canary(db_session, user)

    assert result["application_id"] == first.id
    assert result["attempt_number"] == 2
    assert result["will_retry_same_application"] is True
    assert result["will_move_to_next_application"] is False
    assert attempts == [first.id]


def test_third_failure_marks_blocked_after_3_attempts(db_session, monkeypatch) -> None:
    monkeypatch.setattr(settings, "autonomous_real_submit_enabled", True)
    user = User(email="user@example.com")
    job = _job(assisted_result={"latest_safe_diagnostic": _safe_diag(), "autonomous_fix_attempt_count": 2})
    db_session.add_all([user, job])
    db_session.commit()
    monkeypatch.setattr(autonomous_submit, "assist_apply_application", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("third failure")))
    monkeypatch.setattr(autonomous_submit, "run_assist_apply_probe_background", lambda *args, **kwargs: None)

    result = autonomous_submit.run_autonomous_real_submit_canary(db_session, user)

    assert result["status"] == "blocked_after_3_attempts"
    assert result["application_id"] == job.id
    assert result["attempt_number"] == 3
    assert result["will_retry_same_application"] is False
    assert result["will_move_to_next_application"] is True


def test_after_third_failure_moves_to_next_eligible_application(db_session, monkeypatch) -> None:
    monkeypatch.setattr(settings, "autonomous_real_submit_enabled", True)
    user = User(email="user@example.com")
    blocked = _job(assisted_result={"latest_safe_diagnostic": _safe_diag(), "autonomous_fix_attempt_count": 3, "autonomous_blocked_after_3_attempts": True})
    second = _job(source_job_id="DEF456", canonical_url="https://www.jobserve.com/gb/en/job/DEF456", assisted_result={"latest_safe_diagnostic": _safe_diag("DEF456")})
    db_session.add_all([user, blocked, second])
    db_session.commit()
    attempts = []
    monkeypatch.setattr(
        autonomous_submit,
        "assist_apply_application",
        lambda db, job, user, **kwargs: attempts.append(job.id) or AssistApplyResult(status="submitted", submitted=True, confirmation_text="Your application has been submitted."),
    )

    result = autonomous_submit.run_autonomous_real_submit_canary(db_session, user)

    assert result["submitted"] is True
    assert result["application_id"] == second.id
    assert attempts == [second.id]


def test_timeout_failure_counts_as_failed_attempt(db_session, monkeypatch) -> None:
    monkeypatch.setattr(settings, "autonomous_real_submit_enabled", True)
    user = User(email="user@example.com")
    job = _job()
    db_session.add_all([user, job])
    db_session.commit()
    monkeypatch.setattr(autonomous_submit, "run_assist_apply_probe_background", lambda *args, **kwargs: None)
    monkeypatch.setattr(autonomous_submit, "assist_apply_application", lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("submit_stalled")))

    result = autonomous_submit.run_autonomous_real_submit_canary(db_session, user)

    assert result["attempt_number"] == 1
    assert result["failed_phase"] == "unexpected_canary_exception"
    assert "submit_stalled" in result["exact_error"]


def test_named_timeout_uses_timeout_code_as_failed_phase(db_session, monkeypatch) -> None:
    monkeypatch.setattr(settings, "autonomous_real_submit_enabled", True)
    user = User(email="user@example.com")
    job = _job(
        assisted_result={
            "latest_safe_diagnostic": _safe_diag(),
            "progress": {"current_step": "availability_check_start"},
            "running_step": "availability_check_start",
            "debug_steps": [{"step": "availability_check_start"}],
        }
    )
    db_session.add_all([user, job])
    db_session.commit()
    monkeypatch.setattr(autonomous_submit, "run_assist_apply_probe_background", lambda *args, **kwargs: None)
    monkeypatch.setattr(autonomous_submit, "assist_apply_application", lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("job_load_timeout")))

    result = autonomous_submit.run_autonomous_real_submit_canary(db_session, user)

    assert result["failed_phase"] == "job_load_timeout"
    assert result["exact_error"] == "TimeoutError: job_load_timeout"
    assert result["last_known_stage"] == "availability_check_start"


def test_profile_validation_error_is_not_unexpected_canary_exception(db_session, monkeypatch) -> None:
    monkeypatch.setattr(settings, "autonomous_real_submit_enabled", True)
    user = User(email="")
    job = _job(
        assisted_result={
            "latest_safe_diagnostic": _safe_diag(),
            "progress": {"current_step": "profile_validation_start"},
            "running_step": "profile_validation_start",
            "debug_steps": [{"step": "profile_validation_start"}],
        }
    )
    db_session.add_all([user, job])
    db_session.commit()
    monkeypatch.setattr(autonomous_submit, "run_assist_apply_probe_background", lambda *args, **kwargs: None)
    monkeypatch.setattr(autonomous_submit, "assist_apply_application", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("Email is required before submitting a JobServe application.")))

    result = autonomous_submit.run_autonomous_real_submit_canary(db_session, user)

    assert result["failed_phase"] == "profile_validation"
    assert result["exact_error"] == "ValueError: Email is required before submitting a JobServe application."
    assert result["last_known_stage"] == "profile_validation_start"


def test_already_submitted_moves_on_safely(db_session, monkeypatch) -> None:
    monkeypatch.setattr(settings, "autonomous_real_submit_enabled", True)
    user = User(email="user@example.com")
    submitted = _job(application_status="applied", assisted_result={"latest_safe_diagnostic": _safe_diag(), "submitted": True})
    second = _job(source_job_id="DEF456", canonical_url="https://www.jobserve.com/gb/en/job/DEF456", assisted_result={"latest_safe_diagnostic": _safe_diag("DEF456")})
    db_session.add_all([user, submitted, second])
    db_session.commit()
    attempts = []
    monkeypatch.setattr(
        autonomous_submit,
        "assist_apply_application",
        lambda db, job, user, **kwargs: attempts.append(job.id) or AssistApplyResult(status="submitted", submitted=True, confirmation_text="Your application has been submitted."),
    )

    result = autonomous_submit.run_autonomous_real_submit_canary(db_session, user)

    assert result["submitted"] is True
    assert attempts == [second.id]


def test_submit_failure_after_safe_diagnostic_creates_handoff_when_canary_disabled(db_session, monkeypatch) -> None:
    monkeypatch.setattr(settings, "autonomous_real_submit_enabled", False)
    user = User(email="user@example.com")
    job = _job(assisted_result={"latest_safe_diagnostic": _safe_diag(), "status": "failed", "final_error": "modal closed before final apply"})
    db_session.add_all([user, job])
    db_session.commit()
    monkeypatch.setattr(
        autonomous_submit,
        "create_or_update_codex_handoff",
        lambda report: {"status": "created", "issue_url": "https://github.com/owner/repo/issues/13"},
    )

    result = autonomous_submit.run_autonomous_real_submit_canary(db_session, user)

    assert result["status"] == "failed"
    assert result["failed_phase"] == "submit_failure_after_safe_diagnostic"
    assert result["codex_handoff_status"] == "created"
    assert result["github_issue_url"].endswith("/13")
