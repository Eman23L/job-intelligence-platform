from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.config import settings
from app.db.models import Job, User
from app.diagnostics.assist_apply_runs import new_run_id, run_assist_apply_probe_background
from app.schemas.database import AssistApplyResult
from app.services.apply_agent import assist_apply_application

AUTONOMOUS_ARTIFACT_ROOT = Path("backend/runtime/autonomous_real_submit")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "passed": passed, "detail": detail}


def latest_safe_diagnostic_passed(job: Job) -> bool:
    diagnostic = (job.assisted_result or {}).get("latest_safe_diagnostic") if isinstance(job.assisted_result, dict) else None
    return bool(
        isinstance(diagnostic, dict)
        and diagnostic.get("safe_mode") is True
        and diagnostic.get("submit_allowed") is False
        and diagnostic.get("status") == "passed"
        and diagnostic.get("overall_status") == "ok"
    )


def autonomous_real_submit_verification(job: Job, *, write_artifact: bool = True) -> dict[str, Any]:
    assisted = job.assisted_result if isinstance(job.assisted_result, dict) else {}
    latest_safe = assisted.get("latest_safe_diagnostic") if isinstance(assisted.get("latest_safe_diagnostic"), dict) else {}
    expected_refs = {str(value).strip() for value in [job.source_job_id, job.original_external_id] if str(value or "").strip()}
    safe_ref = str(latest_safe.get("source_job_id") or "").strip()
    safe_title = str(latest_safe.get("job_title") or "").strip().lower()
    safe_company = str(latest_safe.get("job_company") or "").strip().lower()
    checks = [
        _check("feature_enabled", settings.autonomous_real_submit_enabled, "AUTONOMOUS_REAL_SUBMIT_ENABLED must be true."),
        _check("application_status_allowed", job.application_status in {"ready_to_apply", "opened"}, f"status={job.application_status}"),
        _check("not_already_submitted", not bool(assisted.get("submitted") or job.applied_at or job.application_status == "applied"), "Application is not already submitted/applied."),
        _check("not_previously_autonomous_attempted", not bool(assisted.get("autonomous_real_submit_attempted")), "No prior autonomous real-submit attempt for this application."),
        _check("source_is_jobserve", "jobserve" in " ".join([job.canonical_url or "", getattr(job, "apply_url", "") or "", job.source_job_id or ""]).lower(), "Job source/url must be JobServe."),
        _check("latest_safe_diagnostic_passed", latest_safe_diagnostic_passed(job), f"latest_safe_diagnostic_status={latest_safe.get('status')}"),
        _check("exact_job_reference_matches", bool(safe_ref and safe_ref in expected_refs), f"safe_diagnostic_reference={safe_ref or 'missing'} expected={sorted(expected_refs)}"),
        _check("job_title_matches", bool(safe_title and safe_title == (job.title or "").strip().lower()), f"safe_diagnostic_title={latest_safe.get('job_title') or 'missing'} db_title={job.title}"),
        _check("job_company_matches", bool(not job.company_name or (safe_company and safe_company == job.company_name.strip().lower())), f"safe_diagnostic_company={latest_safe.get('job_company') or 'missing'} db_company={job.company_name}"),
        _check("cv_available", latest_safe.get("cv_found") is True, "Safe diagnostic must confirm a CV is available."),
        _check("final_form_checks_delegated_to_submit_guard", True, "Email, work authorization, CV attachment, required fields, validation errors, and account toggle are rechecked in-browser immediately before final Apply."),
    ]
    report = {
        "overall_status": "ok" if all(item["passed"] for item in checks) else "failed",
        "failed_phase": None,
        "exact_error": None,
        "recommended_fix": "No fix required.",
        "checks": checks,
        "application_id": job.id,
        "job_title": job.title,
        "job_company": job.company_name,
        "source_job_id": job.source_job_id,
        "original_external_id": job.original_external_id,
        "latest_safe_diagnostic": latest_safe,
        "created_at": _now(),
    }
    failed = next((item for item in checks if not item["passed"]), None)
    if failed:
        report["failed_phase"] = failed["name"]
        report["exact_error"] = failed["detail"]
        report["recommended_fix"] = "Fix the failed autonomous real-submit verification check, then rerun safe diagnostics."
    if write_artifact:
        artifact_path = _write_verification_report(job.id, report)
        report["artifact_paths"] = [artifact_path]
    else:
        report["artifact_paths"] = []
    return report


def autonomous_submit_status(db: Session, user: User) -> dict[str, Any]:
    jobs = db.scalars(select(Job).where(Job.assisted_result.is_not(None)).order_by(Job.last_apply_attempt_at.desc().nullslast())).all()
    last_result = None
    for job in jobs:
        assisted = job.assisted_result if isinstance(job.assisted_result, dict) else {}
        if assisted.get("autonomous_real_submit_result"):
            last_result = assisted["autonomous_real_submit_result"]
            break
    eligible = [job.id for job in jobs if autonomous_real_submit_verification(job, write_artifact=False)["overall_status"] == "ok"]
    return {
        "enabled": settings.autonomous_real_submit_enabled,
        "max_submits_per_run": settings.max_autonomous_real_submits_per_run,
        "eligible_application_ids": eligible[: settings.max_autonomous_real_submits_per_run],
        "last_result": last_result,
    }


def run_autonomous_real_submit_canary(db: Session, user: User) -> dict[str, Any]:
    if not settings.autonomous_real_submit_enabled:
        return _run_result("disabled", "feature_disabled", "AUTONOMOUS_REAL_SUBMIT_ENABLED is false.", "Enable the flag only when ready for a controlled canary.")
    max_submits = max(0, settings.max_autonomous_real_submits_per_run)
    if max_submits < 1:
        return _run_result("failed", "max_submit_limit_zero", "MAX_AUTONOMOUS_REAL_SUBMITS_PER_RUN must be at least 1.", "Set the canary limit to 1.")

    submitted = 0
    jobs = db.scalars(select(Job).where(Job.application_status.in_(("ready_to_apply", "opened"))).order_by(Job.id)).all()
    for job in jobs:
        report = autonomous_real_submit_verification(job)
        if report["overall_status"] != "ok":
            continue
        result = _attempt_one(db, job, user, report)
        submitted += 1 if result.get("submitted") else 0
        if result.get("status") != "submitted" or submitted >= max_submits:
            return result
    return _run_result("completed", None, None, "No eligible applications found." if submitted == 0 else "Autonomous canary completed.")


def _attempt_one(db: Session, job: Job, user: User, verification_report: dict[str, Any]) -> dict[str, Any]:
    assisted = job.assisted_result if isinstance(job.assisted_result, dict) else {}
    job.assisted_result = {
        **assisted,
        "autonomous_real_submit_attempted": True,
        "autonomous_real_submit_verification": verification_report,
    }
    flag_modified(job, "assisted_result")
    db.commit()
    try:
        result: AssistApplyResult = assist_apply_application(db, job, user, mode="submit_with_confirmation", debug_mode=True)
    except Exception as exc:  # noqa: BLE001
        diagnostic_run_id = new_run_id()
        run_assist_apply_probe_background(diagnostic_run_id, job.id, user.id, safe_mode=True, submit_allowed=False)
        payload = _run_result("failed", "autonomous_submit_exception", str(exc), "Inspect safe diagnostic artifacts and final-submit verification report.", job.id, diagnostic_run_id)
        _persist_result(db, job, payload)
        return payload

    if not result.submitted or result.confirmation_text != "Your application has been submitted.":
        diagnostic_run_id = new_run_id()
        run_assist_apply_probe_background(diagnostic_run_id, job.id, user.id, safe_mode=True, submit_allowed=False)
        payload = _run_result("failed", "success_text_missing", "JobServe submission success text was not confirmed.", "Do not mark applied unless JobServe success text is visible.", job.id, diagnostic_run_id)
        _persist_result(db, job, payload)
        return payload
    payload = _run_result("submitted", None, None, "Autonomous real-submit canary submitted exactly one application.", job.id)
    payload["submitted"] = True
    _persist_result(db, job, payload)
    return payload


def _persist_result(db: Session, job: Job, result: dict[str, Any]) -> None:
    assisted = job.assisted_result if isinstance(job.assisted_result, dict) else {}
    job.assisted_result = {**assisted, "autonomous_real_submit_result": result}
    flag_modified(job, "assisted_result")
    db.commit()


def _write_verification_report(application_id: int, report: dict[str, Any]) -> str:
    AUTONOMOUS_ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = AUTONOMOUS_ARTIFACT_ROOT / f"autonomous_submit_{application_id}_{timestamp}.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return str(path)


def _run_result(status: str, failed_phase: str | None, exact_error: str | None, recommended_fix: str, application_id: int | None = None, diagnostic_run_id: str | None = None) -> dict[str, Any]:
    return {
        "status": status,
        "application_id": application_id,
        "submitted": status == "submitted",
        "failed_phase": failed_phase,
        "exact_error": exact_error,
        "recommended_fix": recommended_fix,
        "diagnostic_run_id": diagnostic_run_id,
        "created_at": _now(),
    }
