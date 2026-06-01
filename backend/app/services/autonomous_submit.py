from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import traceback
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.config import settings
from app.db.models import Job, User
from app.db.session import SessionLocal
from app.diagnostics.assist_apply_runs import new_run_id, run_assist_apply_probe_background
from app.schemas.database import AssistApplyResult
from app.services.apply_agent import BrowserAutomationError, assist_apply_application
from app.services.applications import _candidate_application_rows
from app.services.browser_automation import chromium_diagnostics
from app.services.codex_handoff import create_or_update_codex_handoff, environment_summary

AUTONOMOUS_ARTIFACT_ROOT = Path("backend/runtime/autonomous_real_submit")
SAFE_DIAGNOSTIC_MAX_AGE_SECONDS = 24 * 60 * 60
logger = logging.getLogger(__name__)


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


def autonomous_real_submit_verification(job: Job, *, write_artifact: bool = True, include_feature_check: bool = True) -> dict[str, Any]:
    assisted = job.assisted_result if isinstance(job.assisted_result, dict) else {}
    latest_safe = assisted.get("latest_safe_diagnostic") if isinstance(assisted.get("latest_safe_diagnostic"), dict) else {}
    expected_refs = {str(value).strip() for value in [job.source_job_id, job.original_external_id] if str(value or "").strip()}
    safe_ref = str(latest_safe.get("source_job_id") or "").strip()
    safe_title = str(latest_safe.get("job_title") or "").strip().lower()
    safe_company = str(latest_safe.get("job_company") or "").strip().lower()
    checks = [
        *([_check("feature_enabled", settings.autonomous_real_submit_enabled, "AUTONOMOUS_REAL_SUBMIT_ENABLED must be true.")] if include_feature_check else []),
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
    max_submits = max(0, settings.max_autonomous_real_submits_per_run)
    if max_submits < 1:
        return _run_result("failed", "max_submit_limit_zero", "MAX_AUTONOMOUS_REAL_SUBMITS_PER_RUN must be at least 1.", "Set the canary limit to 1.")

    submitted = 0
    steps: list[dict[str, Any]] = []
    last_blocked_result: dict[str, Any] | None = None
    jobs = _candidate_jobs(db, user)
    for job in jobs:
        step = _orchestrate_one_application(db, job, user)
        steps.append(step)
        if step["final_outcome"] in {"skipped_not_jobserve", "skipped_already_submitted"}:
            continue
        if step["final_outcome"] == "blocked_after_3_attempts":
            continue
        if step["final_outcome"] == "blocked_waiting_for_fix_deploy":
            result = _run_result("waiting_for_fix_deploy", step.get("blocked_reason"), step.get("exact_error"), "Waiting for Codex fix, deploy, and fresh safe diagnostic before retrying this same application.", job.id, step.get("diagnostic_run_id"))
            _attach_attempt_state(result, job.assisted_result if isinstance(job.assisted_result, dict) else {})
            result["orchestration_steps"] = steps
            _persist_result(db, job, result)
            return result
        if step.get("codex_handoff_created"):
            result = _run_result("failed", step.get("failed_phase") or step.get("blocked_reason"), step.get("exact_error"), step.get("recommended_fix") or "Inspect orchestration handoff.", job.id, step.get("diagnostic_run_id"))
            _attach_handoff_result(result, step.get("codex_handoff_result"))
            result["orchestration_steps"] = steps
            _persist_result(db, job, result)
            return result
        if step["final_outcome"] not in {"eligible", "eligible_after_recovery"}:
            continue
        report = autonomous_real_submit_verification(job, include_feature_check=False)
        if report["overall_status"] != "ok":
            step["final_outcome"] = "blocked"
            step["blocked_reason"] = report.get("failed_phase")
            step["exact_error"] = report.get("exact_error")
            step["recommended_fix"] = report.get("recommended_fix")
            continue
        if not settings.autonomous_real_submit_enabled:
            step["final_outcome"] = "blocked_feature_disabled"
            step["blocked_reason"] = "feature_disabled"
            step["exact_error"] = "AUTONOMOUS_REAL_SUBMIT_ENABLED is false."
            step["recommended_fix"] = "Enable the flag only when ready for a controlled canary."
            assisted = job.assisted_result if isinstance(job.assisted_result, dict) else {}
            if assisted.get("final_error") or assisted.get("status") == "failed":
                handoff_result = _create_codex_handoff(
                    job,
                    {
                        "failed_phase": "submit_failure_after_safe_diagnostic",
                        "exact_error": assisted.get("final_error") or "Submit JobServe application failed after safe diagnostic completed.",
                        "recommended_fix": "Safe diagnostic passed. The failure is likely in the final form-fill/final-submit stage.",
                        "orchestration_steps": steps,
                    },
                )
                _attach_handoff_result(step, handoff_result)
                step["codex_handoff_created"] = handoff_result.get("status") in {"created", "updated", "loop_guard_stopped", "failed"}
                step["codex_handoff_result"] = handoff_result
                _record_failed_attempt(db, job, "submit_failure_after_safe_diagnostic", step["exact_error"], None, handoff_result.get("issue_url"))
                _attach_attempt_state(step, job.assisted_result if isinstance(job.assisted_result, dict) else {})
                result = _run_result("failed", "submit_failure_after_safe_diagnostic", step["exact_error"], step["recommended_fix"], job.id)
                _attach_handoff_result(result, handoff_result)
                _attach_attempt_state(result, job.assisted_result if isinstance(job.assisted_result, dict) else {})
                result["orchestration_steps"] = steps
                _persist_result(db, job, result)
                return result
            continue
        result = _attempt_one(db, job, user, report)
        result["orchestration_steps"] = steps
        if result.get("status") == "blocked_after_3_attempts":
            last_blocked_result = result
            steps.append(
                {
                    "application_id": job.id,
                    "inspected": True,
                    "final_outcome": "blocked_after_3_attempts",
                    "action_taken": "move_to_next_application",
                    "attempt_number": result.get("attempt_number"),
                    "max_attempts": result.get("max_attempts"),
                }
            )
            continue
        submitted += 1 if result.get("submitted") else 0
        if result.get("status") != "submitted" or submitted >= max_submits:
            return result
    if last_blocked_result is not None:
        last_blocked_result["orchestration_steps"] = steps
        return last_blocked_result
    result = _run_result("completed", None, None, "No real submit performed. See orchestration_steps for recovered, skipped, and blocked applications.")
    result["orchestration_steps"] = steps
    return result


def run_autonomous_real_submit_canary_background(user_id: int) -> None:
    logger.info("autonomous_submit_background_start user_id=%s", user_id)
    with SessionLocal() as db:
        user = db.get(User, user_id)
        if user is None:
            logger.error("autonomous_submit_background_missing_user user_id=%s", user_id)
            return
        try:
            result = run_autonomous_real_submit_canary(db, user)
            logger.info(
                "autonomous_submit_background_done user_id=%s status=%s application_id=%s failed_phase=%s",
                user_id,
                result.get("status"),
                result.get("application_id"),
                result.get("failed_phase"),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("autonomous_submit_background_failed user_id=%s error=%s", user_id, exc)


def _orchestrate_one_application(db: Session, job: Job, user: User) -> dict[str, Any]:
    step: dict[str, Any] = {
        "application_id": job.id,
        "inspected": True,
        "blocked_reason": None,
        "action_taken": "none",
        "diagnostic_run_id": None,
        "diagnostic_result": None,
        "reset_performed": False,
        "retried": False,
        "final_outcome": "pending",
        "codex_handoff_created": False,
    }
    assisted = job.assisted_result if isinstance(job.assisted_result, dict) else {}
    _attach_attempt_state(step, assisted)
    if job.application_status == "applied" or job.applied_at or assisted.get("submitted"):
        step.update({"blocked_reason": "already_submitted", "final_outcome": "skipped_already_submitted"})
        return step
    if not _is_jobserve(job):
        step.update({"blocked_reason": "not_jobserve", "final_outcome": "skipped_not_jobserve"})
        return step
    if _failure_attempt_count(assisted) >= settings.max_autonomous_fix_attempts_per_application:
        step.update(
            {
                "blocked_reason": "blocked_after_3_attempts",
                "failed_phase": "blocked_after_3_attempts",
                "exact_error": "Application reached the autonomous repair attempt limit.",
                "recommended_fix": "Inspect accumulated Codex handoff issue, diagnostics, and artifacts before manual reset.",
                "final_outcome": "blocked_after_3_attempts",
                "waiting_for_fix_deploy": False,
                "will_retry_same_application": False,
                "will_move_to_next_application": True,
            }
        )
        _persist_orchestration_step(db, job, step)
        return step
    if assisted.get("autonomous_real_submit_attempted") and not _new_code_and_fresh_safe_diagnostic(assisted):
        step.update(
            {
                "blocked_reason": "already_attempted_autonomous_submit",
                "final_outcome": "blocked_waiting_for_fix_deploy",
                "waiting_for_fix_deploy": True,
                "will_retry_same_application": True,
                "will_move_to_next_application": False,
            }
        )
        _persist_orchestration_step(db, job, step)
        return step

    report = autonomous_real_submit_verification(job, write_artifact=False, include_feature_check=False)
    reason = report.get("failed_phase")
    if report["overall_status"] == "ok":
        step["final_outcome"] = "eligible"
        return step
    step["blocked_reason"] = reason
    if reason in {"application_status_allowed", "latest_safe_diagnostic_passed", "exact_job_reference_matches", "job_title_matches", "job_company_matches", "cv_available"} or _safe_diagnostic_too_old(assisted):
        diagnostic_run_id = new_run_id()
        step.update({"action_taken": "safe_diagnostic", "diagnostic_run_id": diagnostic_run_id})
        run_assist_apply_probe_background(diagnostic_run_id, job.id, user.id, safe_mode=True, submit_allowed=False)
        latest_safe = ((job.assisted_result or {}).get("latest_safe_diagnostic") if isinstance(job.assisted_result, dict) else {}) or {}
        if not latest_safe:
            db.refresh(job)
            latest_safe = ((job.assisted_result or {}).get("latest_safe_diagnostic") if isinstance(job.assisted_result, dict) else {}) or {}
        step["diagnostic_result"] = latest_safe
        if latest_safe.get("status") == "passed" and job.application_status not in {"applied"} and not job.applied_at:
            if job.application_status in {"failed", "timeout", "worker_progress_timeout"} or "timeout" in str((job.assisted_result or {}).get("final_error", "")):
                job.application_status = "ready_to_apply"
                step["reset_performed"] = True
                db.commit()
            step["retried"] = True
            retry_report = autonomous_real_submit_verification(job, write_artifact=False, include_feature_check=False)
            if retry_report["overall_status"] == "ok":
                step["final_outcome"] = "eligible_after_recovery"
            else:
                step.update({"final_outcome": "blocked_after_recovery", "blocked_reason": retry_report.get("failed_phase"), "exact_error": retry_report.get("exact_error"), "recommended_fix": retry_report.get("recommended_fix")})
                handoff_result = _create_codex_handoff(job, {**retry_report, "orchestration_steps": [step]})
                _attach_handoff_result(step, handoff_result)
                step["codex_handoff_created"] = True
                step["codex_handoff_result"] = handoff_result
                count = _record_failed_attempt(db, job, step.get("blocked_reason"), step.get("exact_error"), step.get("diagnostic_run_id"), handoff_result.get("issue_url"))
                if count >= settings.max_autonomous_fix_attempts_per_application:
                    step["final_outcome"] = "blocked_after_3_attempts"
                    step["blocked_reason"] = "blocked_after_3_attempts"
                _attach_attempt_state(step, job.assisted_result if isinstance(job.assisted_result, dict) else {})
        else:
            handoff = _codex_handoff(job, latest_safe)
            step.update(handoff)
            step["codex_handoff_created"] = True
            step["final_outcome"] = "diagnostic_failed"
            count = _record_failed_attempt(db, job, step.get("failed_phase"), step.get("exact_error"), step.get("diagnostic_run_id"), step.get("github_issue_url"))
            if count >= settings.max_autonomous_fix_attempts_per_application:
                step["final_outcome"] = "blocked_after_3_attempts"
                step["blocked_reason"] = "blocked_after_3_attempts"
            _attach_attempt_state(step, job.assisted_result if isinstance(job.assisted_result, dict) else {})
    else:
        step.update({"exact_error": report.get("exact_error"), "recommended_fix": report.get("recommended_fix"), "final_outcome": "blocked"})
    _persist_orchestration_step(db, job, step)
    return step


def _candidate_jobs(db: Session, user: User) -> list[Job]:
    rows = _candidate_application_rows(db, user)
    if rows:
        return [job for job, _score in rows]
    return db.scalars(select(Job).where(Job.status != "excluded").order_by(Job.id)).all()


def _attempt_one(db: Session, job: Job, user: User, verification_report: dict[str, Any]) -> dict[str, Any]:
    _log_autonomous_browser_preflight(job)
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
    except BrowserAutomationError as exc:
        diagnostic_run_id = new_run_id()
        run_assist_apply_probe_background(diagnostic_run_id, job.id, user.id, safe_mode=True, submit_allowed=False)
        failed_phase = "browser_launch" if exc.error in {"browser_launch", "chromium_not_installed", "playwright_not_installed"} else exc.error
        payload = _run_result("failed", failed_phase, exc.message, "Fix browser automation startup in Render, then rerun safe diagnostics.", job.id, diagnostic_run_id)
        handoff_result = _create_codex_handoff(
            job,
            {
                **payload,
                "traceback": traceback.format_exc(),
                "orchestration_steps": (job.assisted_result or {}).get("autonomous_orchestration_steps") or [],
            },
        )
        _attach_handoff_result(payload, handoff_result)
        count = _record_failed_attempt(db, job, payload.get("failed_phase"), payload.get("exact_error"), diagnostic_run_id, payload.get("github_issue_url"))
        if count >= settings.max_autonomous_fix_attempts_per_application:
            payload["status"] = "blocked_after_3_attempts"
        _attach_attempt_state(payload, job.assisted_result if isinstance(job.assisted_result, dict) else {})
        _persist_result(db, job, payload)
        return payload
    except Exception as exc:  # noqa: BLE001
        diagnostic_run_id = new_run_id()
        run_assist_apply_probe_background(diagnostic_run_id, job.id, user.id, safe_mode=True, submit_allowed=False)
        payload = _run_result("failed", "autonomous_submit_exception", str(exc), "Inspect safe diagnostic artifacts and final-submit verification report.", job.id, diagnostic_run_id)
        handoff_result = _create_codex_handoff(
            job,
            {
                **payload,
                "traceback": traceback.format_exc(),
                "orchestration_steps": (job.assisted_result or {}).get("autonomous_orchestration_steps") or [],
            },
        )
        _attach_handoff_result(payload, handoff_result)
        count = _record_failed_attempt(db, job, payload.get("failed_phase"), payload.get("exact_error"), diagnostic_run_id, payload.get("github_issue_url"))
        if count >= settings.max_autonomous_fix_attempts_per_application:
            payload["status"] = "blocked_after_3_attempts"
        _attach_attempt_state(payload, job.assisted_result if isinstance(job.assisted_result, dict) else {})
        _persist_result(db, job, payload)
        return payload

    if not result.submitted or not _confirmation_means_submitted(result.confirmation_text):
        diagnostic_run_id = new_run_id()
        run_assist_apply_probe_background(diagnostic_run_id, job.id, user.id, safe_mode=True, submit_allowed=False)
        payload = _run_result("failed", "success_text_missing", "JobServe submission success text was not confirmed.", "Do not mark applied unless JobServe success text is visible.", job.id, diagnostic_run_id)
        handoff_result = _create_codex_handoff(
            job,
            {
                **payload,
                "failed_phase": "success_text_missing",
                "exact_error": "JobServe submission success text was not confirmed.",
                "recommended_fix": "Failure is likely in the final form-fill/final-submit stage. Inspect before/after submit artifacts.",
                "orchestration_steps": (job.assisted_result or {}).get("autonomous_orchestration_steps") or [],
                "artifact_links": [*result.screenshot_urls, *result.html_snapshot_urls, *result.screenshot_paths, *result.html_snapshot_paths],
            },
        )
        _attach_handoff_result(payload, handoff_result)
        count = _record_failed_attempt(db, job, payload.get("failed_phase"), payload.get("exact_error"), diagnostic_run_id, payload.get("github_issue_url"))
        if count >= settings.max_autonomous_fix_attempts_per_application:
            payload["status"] = "blocked_after_3_attempts"
        _attach_attempt_state(payload, job.assisted_result if isinstance(job.assisted_result, dict) else {})
        _persist_result(db, job, payload)
        return payload
    payload = _run_result("submitted", None, None, "Autonomous real-submit canary submitted exactly one application.", job.id)
    payload["submitted"] = True
    _persist_result(db, job, payload)
    return payload


def _confirmation_means_submitted(value: str | None) -> bool:
    if not value:
        return False
    lowered = value.lower()
    return any(text in lowered for text in ["your application has been submitted", "application received", "already applied", "already submitted", "you have applied"])


def _log_autonomous_browser_preflight(job: Job) -> None:
    diagnostics = chromium_diagnostics()
    logger.info(
        "autonomous_submit_browser_preflight application_id=%s service_type=%s playwright_enabled=%s playwright_browsers_path=%s chromium_executable_path=%s chromium_file_exists=%s chromium_file_executable=%s",
        job.id,
        settings.service_type,
        settings.playwright_enabled,
        diagnostics.get("playwright_browsers_path"),
        diagnostics.get("chromium_executable_path"),
        diagnostics.get("chromium_file_exists"),
        diagnostics.get("chromium_file_executable"),
    )


def _persist_result(db: Session, job: Job, result: dict[str, Any]) -> None:
    assisted = job.assisted_result if isinstance(job.assisted_result, dict) else {}
    job.assisted_result = {**assisted, "autonomous_real_submit_result": result}
    flag_modified(job, "assisted_result")
    db.commit()


def _persist_orchestration_step(db: Session, job: Job, step: dict[str, Any]) -> None:
    assisted = job.assisted_result if isinstance(job.assisted_result, dict) else {}
    existing_steps = assisted.get("autonomous_orchestration_steps") if isinstance(assisted.get("autonomous_orchestration_steps"), list) else []
    job.assisted_result = {**assisted, "autonomous_orchestration_steps": [*existing_steps, step]}
    flag_modified(job, "assisted_result")
    db.commit()


def _failure_attempt_count(assisted: dict[str, Any]) -> int:
    try:
        return int(assisted.get("autonomous_fix_attempt_count") or 0)
    except (TypeError, ValueError):
        return 0


def _record_failed_attempt(db: Session, job: Job, failed_phase: str | None, exact_error: str | None, diagnostic_run_id: str | None, github_issue_url: str | None) -> int:
    assisted = job.assisted_result if isinstance(job.assisted_result, dict) else {}
    attempts = assisted.get("autonomous_fix_attempts") if isinstance(assisted.get("autonomous_fix_attempts"), list) else []
    count = _failure_attempt_count(assisted) + 1
    entry = {
        "attempt": count,
        "failed_phase": failed_phase,
        "exact_error": exact_error,
        "diagnostic_run_id": diagnostic_run_id,
        "github_issue_url": github_issue_url,
        "code_revision": _code_revision(),
        "created_at": _now(),
    }
    job.assisted_result = {
        **assisted,
        "autonomous_fix_attempt_count": count,
        "autonomous_fix_attempts": [*attempts, entry],
        "autonomous_waiting_for_fix_deploy": count < settings.max_autonomous_fix_attempts_per_application,
        "autonomous_blocked_after_3_attempts": count >= settings.max_autonomous_fix_attempts_per_application,
    }
    flag_modified(job, "assisted_result")
    db.commit()
    return count


def _attach_attempt_state(payload: dict[str, Any], assisted: dict[str, Any]) -> None:
    count = _failure_attempt_count(assisted)
    max_attempts = settings.max_autonomous_fix_attempts_per_application
    payload["focused_application_id"] = payload.get("application_id")
    payload["attempt_number"] = count
    payload["max_attempts"] = max_attempts
    payload["waiting_for_fix_deploy"] = bool(assisted.get("autonomous_waiting_for_fix_deploy"))
    payload["will_retry_same_application"] = count > 0 and count < max_attempts
    payload["will_move_to_next_application"] = count >= max_attempts


def _is_jobserve(job: Job) -> bool:
    return "jobserve" in " ".join([job.canonical_url or "", job.source_job_id or "", job.original_external_id or ""]).lower()


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _safe_diagnostic_too_old(assisted: dict[str, Any]) -> bool:
    latest = assisted.get("latest_safe_diagnostic") if isinstance(assisted.get("latest_safe_diagnostic"), dict) else {}
    completed = _parse_time(latest.get("completed_at"))
    if completed is None:
        return False
    if completed.tzinfo is None:
        completed = completed.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - completed).total_seconds() > SAFE_DIAGNOSTIC_MAX_AGE_SECONDS


def _new_code_and_fresh_safe_diagnostic(assisted: dict[str, Any]) -> bool:
    result = assisted.get("autonomous_real_submit_result") if isinstance(assisted.get("autonomous_real_submit_result"), dict) else {}
    latest = assisted.get("latest_safe_diagnostic") if isinstance(assisted.get("latest_safe_diagnostic"), dict) else {}
    if not latest_safe_diagnostic_passed(type("JobLike", (), {"assisted_result": assisted})()):
        return False
    current_revision = _code_revision()
    previous_revision = result.get("code_revision")
    latest_completed = _parse_time(latest.get("completed_at"))
    attempt_created = _parse_time(result.get("created_at"))
    return bool(current_revision and previous_revision and current_revision != previous_revision and latest_completed and attempt_created and latest_completed > attempt_created)


def _code_revision() -> str:
    return os.environ.get("RENDER_GIT_COMMIT") or os.environ.get("GITHUB_SHA") or settings.app_version


def _codex_handoff(job: Job, diagnostic: dict[str, Any]) -> dict[str, Any]:
    failed_phase = diagnostic.get("failed_phase") or "safe_diagnostic_failed"
    exact_error = diagnostic.get("exact_error") or "Safe diagnostic did not pass."
    recommended_fix = diagnostic.get("recommended_fix") or "Inspect safe diagnostic report and artifacts."
    artifacts = diagnostic.get("artifact_links") or []
    report = _handoff_report(
        job,
        {
            "failed_phase": failed_phase,
            "exact_error": exact_error,
            "recommended_fix": recommended_fix,
            "artifact_links": artifacts,
            "diagnostic": diagnostic,
        },
    )
    handoff_result = create_or_update_codex_handoff(report)
    return {
        "failed_phase": failed_phase,
        "exact_error": exact_error,
        "recommended_fix": recommended_fix,
        "artifact_links": artifacts,
        "codex_handoff_status": handoff_result.get("status"),
        "github_issue_url": handoff_result.get("issue_url"),
        "codex_handoff_error": handoff_result.get("error"),
        "codex_handoff_result": handoff_result,
        "codex_handoff": {
            "title": f"Autonomous canary failure for application {job.id}: {failed_phase}",
            "labels": ["codex", "autonomous-canary", "assist-apply"],
        },
    }


def _create_codex_handoff(job: Job, report: dict[str, Any]) -> dict[str, Any]:
    return create_or_update_codex_handoff(_handoff_report(job, report))


def _handoff_report(job: Job, report: dict[str, Any]) -> dict[str, Any]:
    return {
        **report,
        "application_id": job.id,
        "job_title": job.title,
        "job_company": job.company_name,
        "source_job_id": job.source_job_id,
        "original_external_id": job.original_external_id,
        "selected_url": job.canonical_url,
        "environment_summary": environment_summary(),
        "latest_commit_sha": _code_revision(),
    }


def _attach_handoff_result(payload: dict[str, Any], handoff_result: dict[str, Any] | None) -> None:
    if not handoff_result:
        return
    payload["codex_handoff_status"] = handoff_result.get("status")
    payload["github_issue_url"] = handoff_result.get("issue_url")
    payload["codex_handoff_error"] = handoff_result.get("error")
    payload["codex_handoff_attempt_count"] = handoff_result.get("attempt_count")


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
        "codex_handoff_status": None,
        "github_issue_url": None,
        "codex_handoff_error": None,
        "focused_application_id": application_id,
        "attempt_number": 0,
        "max_attempts": settings.max_autonomous_fix_attempts_per_application,
        "waiting_for_fix_deploy": False,
        "will_retry_same_application": False,
        "will_move_to_next_application": False,
        "code_revision": _code_revision(),
        "created_at": _now(),
    }
