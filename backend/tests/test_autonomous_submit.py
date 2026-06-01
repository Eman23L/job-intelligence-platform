from __future__ import annotations

from app.config import settings
from app.db.models import Job, User
from app.schemas.database import AssistApplyResult
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


def test_canary_failure_creates_codex_handoff_issue(db_session, monkeypatch) -> None:
    monkeypatch.setattr(settings, "autonomous_real_submit_enabled", True)
    user = User(email="user@example.com")
    job = _job()
    db_session.add_all([user, job])
    db_session.commit()
    monkeypatch.setattr(autonomous_submit, "run_assist_apply_probe_background", lambda *args, **kwargs: None)
    monkeypatch.setattr(autonomous_submit, "assist_apply_application", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("submit failed")))
    monkeypatch.setattr(
        autonomous_submit,
        "create_or_update_codex_handoff",
        lambda report: {"status": "created", "issue_url": "https://github.com/owner/repo/issues/12"},
    )

    result = autonomous_submit.run_autonomous_real_submit_canary(db_session, user)

    assert result["status"] == "failed"
    assert result["failed_phase"] == "autonomous_submit_exception"
    assert result["codex_handoff_status"] == "created"
    assert result["github_issue_url"].endswith("/12")


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
