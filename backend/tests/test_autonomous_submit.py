from __future__ import annotations

from app.config import settings
from app.db.models import Job, User
from app.schemas.database import AssistApplyResult
from app.services import autonomous_submit


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
