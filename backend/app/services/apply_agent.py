from __future__ import annotations

from dataclasses import dataclass
import logging
import mimetypes
import os
from pathlib import Path
import re
import time
import traceback
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import Job, JobScore, User
from app.db.session import SessionLocal
from app.schemas.database import AssistApplyResult
from app.services.browser_automation import chromium_diagnostics, chromium_executable_path, validate_browser_automation_availability
from app.services.job_availability import check_job_availability
from app.services.profile import get_profile
from app.services.run_tracking import utcnow

logger = logging.getLogger(__name__)

ALLOWED_APPLICATION_STATUSES = {"ready_to_apply", "opened"}
ASSIST_MODES = {"review_only", "submit_with_confirmation"}
LEGAL_FIELD_PATTERN = re.compile(r"\b(visa|sponsor|sponsorship|authorized|authorised|eligibility|criminal|disability|veteran)\b", re.I)
SUBMIT_PATTERN = re.compile(r"\b(submit|send application|apply now|final)\b", re.I)
_OPEN_REVIEW_BROWSERS: list[Any] = []
DEBUG_ARTIFACT_DIR = Path("backend/runtime/apply_debug")
WORKER_CV_DIR = Path("backend/runtime/worker_cv_files")
JOBSERVE_SEARCH_URL = "https://www.jobserve.com/gb/en/Job-Search/"
JOBSERVE_DEFAULTS = {
    "search_keywords": "AI",
    "search_location": "London",
    "search_distance": "Within 50 miles",
    "posted_within": "Within 7 days",
    "job_type": "Any",
    "working_status": "UK Citizen",
}
JOBSERVE_REQUIRED_DROPDOWN_PATTERNS = {
    "availability_notice": [r"availability", r"notice"],
    "salary_expectation_gbp": [r"salary expectation", r"salary"],
    "travel_distance_miles": [r"travel distance", r"travel"],
    "work_authorization": [r"working status", r"work status", r"status in uk", r"eligible.*uk"],
}


class BrowserAutomationError(RuntimeError):
    def __init__(self, error: str, message: str) -> None:
        super().__init__(message)
        self.error = error
        self.message = message


@dataclass(frozen=True)
class FieldCandidate:
    key: str
    value: str
    reason: str


def assist_apply_application(db: Session, job: Job, user: User, *, mode: str = "review_only", debug_mode: bool = False, browser_runner=None) -> AssistApplyResult:
    if mode not in ASSIST_MODES:
        raise ValueError("Invalid assisted apply mode.")
    _validate_application(job)
    availability = check_job_availability(db, job)
    if availability.availability_status != "active":
        raise ValueError(f"Application assistance blocked because job is {availability.availability_status}. {availability.availability_reason or ''}".strip())
    _validate_application(job)

    started_at = utcnow()
    job.assisted_started_at = started_at
    job.last_apply_attempt_at = started_at
    db.commit()
    progress_started = time.perf_counter()

    def progress_callback(step: str, payload: dict[str, Any]) -> None:
        _persist_assist_progress(db, job, step, payload, progress_started)

    profile = get_profile(db, user)
    if mode == "submit_with_confirmation":
        _validate_jobserve_submit(db, job, user, profile)
    candidates = profile_field_candidates(user, profile)
    warnings = _safety_warnings(job)
    try:
        if browser_runner:
            result = browser_runner(job.canonical_url, candidates, profile, mode, job.apply_strategy)
        elif debug_mode:
            result = run_playwright_assist(
                job.canonical_url,
                candidates,
                profile=profile,
                mode=mode,
                apply_strategy=job.apply_strategy,
                debug_mode=True,
                profile_diagnostics=profile_debug_payload(user, profile, candidates),
                job_context=jobserve_job_context(job),
                progress_callback=progress_callback,
            )
        else:
            result = run_playwright_assist(
                job.canonical_url,
                candidates,
                profile=profile,
                mode=mode,
                apply_strategy=job.apply_strategy,
                profile_diagnostics=profile_debug_payload(user, profile, candidates),
                job_context=jobserve_job_context(job),
                progress_callback=progress_callback,
            )
    except BrowserAutomationError:
        raise
    except RuntimeError as exc:
        raise ValueError(str(exc)) from exc

    result.warnings[:] = [*warnings, *result.warnings]
    job.assisted_result = result.model_dump()
    job.assisted_warnings = result.warnings
    logger.info(
        "assist_apply_result_persisted application_id=%s mode=%s debug_mode=%s status=%s screenshots=%s html_snapshots=%s debug_steps=%s final_url=%s final_error=%s",
        job.id,
        mode,
        debug_mode,
        result.status,
        len(result.screenshot_paths),
        len(result.html_snapshot_paths),
        len(result.debug_steps),
        result.final_url,
        result.final_error,
    )
    if result.submitted:
        job.application_status = "applied"
        job.applied_at = utcnow()
    else:
        job.application_status = "opened"
    db.commit()
    return result


def run_assist_apply_background(application_id: int, user_id: int, mode: str = "review_only", debug_mode: bool = False) -> None:
    logger.info(
        "assist_apply_worker_start service_type=%s application_id=%s user_id=%s mode=%s debug_mode=%s",
        settings.service_type,
        application_id,
        user_id,
        mode,
        debug_mode,
    )
    with SessionLocal() as db:
        job = db.get(Job, application_id)
        user = db.get(User, user_id)
        if job is None or user is None:
            logger.error("assist_apply_worker_missing_record service_type=%s application_id=%s user_id=%s", settings.service_type, application_id, user_id)
            return
        try:
            assist_apply_application(db, job, user, mode=mode, debug_mode=debug_mode)
        except BrowserAutomationError as exc:
            _store_assist_failure(db, job, exc.message, error=exc.error)
            logger.exception(
                "assist_apply_worker_browser_error service_type=%s application_id=%s error_code=%s error=%s",
                settings.service_type,
                application_id,
                exc.error,
                exc.message,
            )
        except Exception as exc:  # noqa: BLE001
            _store_assist_failure(db, job, str(exc))
            logger.exception("assist_apply_worker_failed service_type=%s application_id=%s error=%s", settings.service_type, application_id, exc)
        else:
            logger.info("assist_apply_worker_completed service_type=%s application_id=%s", settings.service_type, application_id)


def queued_assist_apply_result() -> AssistApplyResult:
    return AssistApplyResult(
        status="queued",
        filled_fields=[],
        unfilled_fields=[],
        unfilled_required_fields=[],
        uploaded_cv=False,
        submitted=False,
        warnings=["Assisted apply queued on the browser automation worker."],
        screenshot_path=None,
        progress={"current_step": "queued", "elapsed_ms": 0},
    )


def _store_assist_failure(db: Session, job: Job, message: str, *, error: str | None = None) -> None:
    result = AssistApplyResult(
        status="failed",
        filled_fields=[],
        unfilled_fields=[],
        unfilled_required_fields=[],
        uploaded_cv=False,
        submitted=False,
        warnings=[f"{error}: {message}" if error else message],
        screenshot_path=None,
    )
    job.assisted_result = result.model_dump()
    job.assisted_warnings = result.warnings
    job.last_apply_attempt_at = utcnow()
    db.commit()


def _persist_assist_progress(db: Session, job: Job, step: str, payload: dict[str, Any], started_perf: float) -> None:
    existing = job.assisted_result or {}
    progress = {
        "current_step": step,
        "elapsed_ms": int((time.perf_counter() - started_perf) * 1000),
        "last_heartbeat_at": utcnow().isoformat(),
        "message": _progress_message(step),
    }
    job.assisted_result = {
        **existing,
        "status": existing.get("status") or "running",
        "filled_fields": existing.get("filled_fields", []),
        "unfilled_fields": existing.get("unfilled_fields", []),
        "unfilled_required_fields": existing.get("unfilled_required_fields", []),
        "uploaded_cv": existing.get("uploaded_cv", False),
        "submitted": existing.get("submitted", False),
        "warnings": existing.get("warnings", []),
        "screenshot_path": existing.get("screenshot_path"),
        "progress": progress,
        "timing_diagnostics": {**existing.get("timing_diagnostics", {}), "total_runtime_ms": progress["elapsed_ms"]},
        "debug_steps": [*existing.get("debug_steps", []), {"step": step, **payload}][-100:],
    }
    job.last_apply_attempt_at = utcnow()
    db.commit()


def _progress_message(step: str) -> str:
    if "browser" in step:
        return "browser startup"
    if "search" in step:
        return "waiting on JobServe"
    if "cv_upload" in step:
        return "uploading CV"
    if "submit" in step or "final_apply" in step or "confirmation" in step:
        return "submitting application"
    return step.replace("_", " ")


def profile_field_candidates(user: User, profile) -> dict[str, FieldCandidate]:
    preferences = profile.preferences if profile is not None and profile.preferences else {}
    raw_values = {
        "email": getattr(profile, "email", None) or user.email,
        "first_name": getattr(profile, "first_name", None),
        "last_name": getattr(profile, "last_name", None),
        "name": preferences.get("name") or preferences.get("full_name"),
        "phone": getattr(profile, "phone", None) or preferences.get("phone") or preferences.get("phone_number"),
        "address": getattr(profile, "address", None),
        "country": getattr(profile, "country", None),
        "location": profile.location_preference if profile is not None else preferences.get("location"),
        "linkedin": preferences.get("linkedin") or preferences.get("linkedin_url"),
        "portfolio": preferences.get("portfolio") or preferences.get("portfolio_url") or preferences.get("website"),
        "salary": getattr(profile, "salary_expectation", None) or preferences.get("salary_expectation") or preferences.get("salary"),
        "travel_distance": getattr(profile, "travel_distance", None),
        "availability_notice": getattr(profile, "availability_notice", None),
        "salary_expectation_gbp": getattr(profile, "salary_expectation_gbp", None),
        "travel_distance_miles": getattr(profile, "travel_distance_miles", None),
        "work_authorization": getattr(profile, "work_status_uk", None) or preferences.get("work_authorization") or JOBSERVE_DEFAULTS["working_status"],
    }
    return {
        key: FieldCandidate(key=key, value=str(value).strip(), reason="Saved profile value")
        for key, value in raw_values.items()
        if value is not None and str(value).strip()
    }


def profile_debug_payload(user: User, profile, candidates: dict[str, FieldCandidate]) -> dict[str, Any]:
    profile_values = {
        "email": getattr(profile, "email", None) or user.email,
        "availability_notice": getattr(profile, "availability_notice", None) if profile is not None else None,
        "salary_expectation": getattr(profile, "salary_expectation", None) if profile is not None else None,
        "salary_expectation_gbp": getattr(profile, "salary_expectation_gbp", None) if profile is not None else None,
        "travel_distance": getattr(profile, "travel_distance", None) if profile is not None else None,
        "travel_distance_miles": getattr(profile, "travel_distance_miles", None) if profile is not None else None,
        "work_status_uk": getattr(profile, "work_status_uk", None) if profile is not None else None,
        "cv_file_name": getattr(profile, "cv_file_name", None) if profile is not None else None,
        "cv_file_path": getattr(profile, "cv_file_path", None) if profile is not None else None,
        "cv_file_mime_type": getattr(profile, "cv_file_mime_type", None) if profile is not None else None,
        "cv_file_size": getattr(profile, "cv_file_size", None) if profile is not None else None,
        "cv_blob_present": bool(getattr(profile, "cv_file_bytes", None)) if profile is not None else False,
    }
    return {
        "profile_loaded": profile is not None,
        "loaded_profile_values": profile_values,
        "candidate_keys": sorted(candidates.keys()),
        "mapped_fields": {},
    }


def jobserve_job_context(job: Job) -> dict[str, Any]:
    return {
        "job_id": job.id,
        "title": job.title,
        "original_title": job.original_title,
        "company_name": job.company_name,
        "original_company": job.original_company,
        "source_job_id": job.source_job_id,
        "original_external_id": job.original_external_id,
        "canonical_url": job.canonical_url,
    }


def classify_form_field(label_text: str, input_type: str = "", autocomplete: str = "", name: str = "", placeholder: str = "") -> str | None:
    text = " ".join([label_text, input_type, autocomplete, name, placeholder]).lower()
    if not text.strip():
        return None
    if "linkedin" in text:
        return "linkedin"
    if "portfolio" in text or "website" in text:
        return "portfolio"
    if "email" in text:
        return "email"
    if "first name" in text or autocomplete == "given-name":
        return "first_name"
    if "last name" in text or "surname" in text or autocomplete == "family-name":
        return "last_name"
    if "phone" in text or "mobile" in text or "tel" in text:
        return "phone"
    if "salary" in text or "compensation" in text:
        return "salary"
    if "location" in text or "city" in text or "address" in text:
        return "location"
    if LEGAL_FIELD_PATTERN.search(text):
        return "work_authorization"
    if "full name" in text or autocomplete == "name":
        return "name"
    return None


def run_playwright_assist(
    url: str,
    candidates: dict[str, FieldCandidate],
    *,
    profile=None,
    mode: str = "review_only",
    apply_strategy: str = "unknown",
    debug_mode: bool = False,
    profile_diagnostics: dict[str, Any] | None = None,
    job_context: dict[str, Any] | None = None,
    progress_callback: Callable[[str, dict[str, Any]], None] | None = None,
) -> AssistApplyResult:
    total_started = time.perf_counter()
    timing_diagnostics: dict[str, Any] = {}
    diagnostics = chromium_diagnostics()
    logger.info(
        "assist_apply_browser_preflight service_type=%s playwright_browsers_path=%s chromium_executable_path=%s chromium_file_exists=%s chromium_file_executable=%s",
        settings.service_type,
        diagnostics["playwright_browsers_path"],
        diagnostics["chromium_executable_path"],
        diagnostics["chromium_file_exists"],
        diagnostics["chromium_file_executable"],
    )
    availability = validate_browser_automation_availability(require_worker=settings.queue_enabled)
    if not availability.available:
        raise BrowserAutomationError(availability.error or "worker_unavailable", availability.message or "Browser automation worker is offline.")
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # noqa: BLE001
        raise BrowserAutomationError("playwright_not_installed", "Playwright is not installed in this environment.") from exc

    headless = settings.app_env.lower() in {"production", "prod", "render"}
    filled: list[str] = []
    unfilled: list[str] = []
    warnings: list[str] = []
    try:
        with sync_playwright() as playwright:
            executable_path = chromium_executable_path()
            launch_options = {"headless": headless}
            if executable_path:
                launch_options["executable_path"] = executable_path
            browser_started = time.perf_counter()
            if progress_callback:
                progress_callback("browser_startup", {"launch_options": {key: value for key, value in launch_options.items() if key != "executable_path"}})
            browser = playwright.chromium.launch(**launch_options)
            timing_diagnostics["browser_startup_ms"] = int((time.perf_counter() - browser_started) * 1000)
            keep_open_for_review = not headless
            try:
                page = browser.new_page()
                page.set_default_timeout(settings.playwright_step_timeout_ms)
                page.set_default_navigation_timeout(settings.page_navigation_timeout_ms)
                if apply_strategy == "jobserve_apply_easy":
                    try:
                        result = _run_jobserve_search_to_apply(
                            page,
                            browser,
                            candidates,
                            profile,
                            job_context or {"canonical_url": url},
                            mode=mode,
                            keep_open_for_review=keep_open_for_review,
                            debug_mode=debug_mode,
                            profile_diagnostics=profile_diagnostics,
                            progress_callback=progress_callback,
                        )
                        result.timing_diagnostics = {**result.timing_diagnostics, **timing_diagnostics, "total_runtime_ms": int((time.perf_counter() - total_started) * 1000)}
                        return result
                    except RuntimeError as exc:
                        if "No matching JobServe search result found" not in str(exc):
                            raise
                        logger.warning("jobserve_search_to_apply_no_match_falling_back error=%s", exc)
                        page.goto(url, wait_until="domcontentloaded", timeout=20000)
                        result = _run_jobserve_modal(
                            page,
                            browser,
                            candidates,
                            profile,
                            mode=mode,
                            keep_open_for_review=keep_open_for_review,
                            debug_mode=debug_mode,
                            profile_diagnostics=profile_diagnostics,
                            progress_callback=progress_callback,
                        )
                        result.timing_diagnostics = {**result.timing_diagnostics, **timing_diagnostics, "total_runtime_ms": int((time.perf_counter() - total_started) * 1000)}
                        return result
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
                if _captcha_visible(page):
                    warnings.append("Captcha detected; manual review required.")
                fields = page.locator("input, textarea, select").all()
                for field in fields:
                    try:
                        tag = field.evaluate("element => element.tagName.toLowerCase()")
                        input_type = str(field.get_attribute("type") or "").lower()
                        if input_type in {"hidden", "password", "submit", "button", "checkbox", "radio"}:
                            continue
                        label_text = _field_label(field)
                        key = classify_form_field(
                            label_text=label_text,
                            input_type=input_type,
                            autocomplete=str(field.get_attribute("autocomplete") or ""),
                            name=str(field.get_attribute("name") or ""),
                            placeholder=str(field.get_attribute("placeholder") or ""),
                        )
                        if input_type == "file":
                            warnings.append("CV upload field detected; upload is disabled until CV file support is configured.")
                            continue
                        if not key:
                            unfilled.append(label_text or str(field.get_attribute("name") or "unknown field"))
                            continue
                        if key == "work_authorization" and key not in candidates:
                            unfilled.append(label_text or "work authorization")
                            continue
                        candidate = candidates.get(key)
                        if candidate is None:
                            unfilled.append(label_text or key)
                            continue
                        if tag == "select":
                            unfilled.append(label_text or key)
                            continue
                        field.fill(candidate.value)
                        filled.append(label_text or key)
                    except Exception as exc:  # noqa: BLE001
                        warnings.append(f"Could not inspect or fill a field: {exc}")
                if _submit_visible(page):
                    warnings.append("Submit control detected and intentionally not clicked.")
                if keep_open_for_review:
                    warnings.append("Browser left open for manual review; close it after reviewing the application.")
                return AssistApplyResult(status="review_required", filled_fields=_dedupe(filled), unfilled_fields=_dedupe(unfilled), unfilled_required_fields=[], uploaded_cv=False, submitted=False, warnings=_dedupe(warnings), screenshot_path=None, timing_diagnostics={**timing_diagnostics, "total_runtime_ms": int((time.perf_counter() - total_started) * 1000)})
            finally:
                if keep_open_for_review:
                    _OPEN_REVIEW_BROWSERS.append(browser)
                else:
                    browser.close()
    except PlaywrightError as exc:
        logger.exception("apply_agent_browser_failed url=%s error=%s", url, exc)
        message = str(exc)
        if "Executable doesn't exist" in message or "playwright install" in message:
            raise BrowserAutomationError("chromium_not_installed", "Playwright Chromium is not installed in this environment.") from exc
        raise BrowserAutomationError("worker_unavailable", "Browser automation worker is offline.") from exc


def _validate_application(job: Job) -> None:
    if job.application_status not in ALLOWED_APPLICATION_STATUSES:
        raise ValueError("Application must be ready_to_apply or opened before assisted apply.")
    if job.apply_strategy == "blocked" or job.apply_difficulty == "blocked":
        raise ValueError("Blocked apply routes cannot be assisted.")
    if not job.canonical_url:
        raise ValueError("Missing apply URL.")


def _validate_jobserve_submit(db: Session, job: Job, user: User, profile) -> None:
    if job.apply_strategy != "jobserve_apply_easy":
        raise ValueError("Submit with confirmation is only available for JobServe easy apply.")
    if not profile or not (getattr(profile, "cv_file_path", None) or getattr(profile, "cv_file_bytes", None)):
        raise ValueError("Saved CV file is required before submitting a JobServe application.")
    if not (getattr(profile, "email", None) or ""):
        raise ValueError("Email is required before submitting a JobServe application.")
    preferences = getattr(profile, "preferences", None) or {}
    if not (getattr(profile, "work_status_uk", None) or preferences.get("jobserve_working_status") or JOBSERVE_DEFAULTS["working_status"]):
        raise ValueError("JobServe working status missing")
    score = db.scalar(select(JobScore).where(JobScore.job_id == job.id, JobScore.user_id == user.id).order_by(JobScore.scored_at.desc()))
    threshold = int(getattr(profile, "minimum_apply_score", None) or 80)
    total = float(score.total_score) if score is not None and score.total_score is not None else 0.0
    if total < threshold:
        raise ValueError(f"Job score {round(total):g} is below your apply threshold of {threshold}.")


def _safety_warnings(job: Job) -> list[str]:
    warnings = [
        "Assisted apply will not submit the application.",
        "Legal or eligibility questions are only filled when exact saved profile data exists.",
    ]
    if job.apply_difficulty == "hard":
        warnings.append("Hard application flow detected; expect manual review.")
    return warnings


class _ApplyDebugRecorder:
    def __init__(self, page, browser, *, enabled: bool, prefix: str = "jobserve", progress_callback: Callable[[str, dict[str, Any]], None] | None = None) -> None:
        self.page = page
        self.browser = browser
        self.enabled = enabled
        self.started_ms = int(time.time() * 1000)
        self.dir = DEBUG_ARTIFACT_DIR / str(self.started_ms)
        self.prefix = prefix
        self.screenshot_paths: list[str] = []
        self.html_snapshot_paths: list[str] = []
        self.steps: list[dict[str, Any]] = []
        self.final_error: str | None = None
        self.progress_callback = progress_callback
        if enabled:
            self.dir.mkdir(parents=True, exist_ok=True)

    def step(self, name: str, **extra: Any) -> None:
        state: dict[str, Any] = {"step": name, **_page_state(self.page, self.browser), **extra}
        self.steps.append(state)
        logger.info("jobserve_apply_step %s", state)
        if self.progress_callback is not None:
            self.progress_callback(name, state)

    def screenshot(self, name: str) -> None:
        if not self.enabled:
            return
        path = self.dir / f"{len(self.screenshot_paths) + 1:02d}_{_slug(name)}.jpg"
        try:
            self.page.screenshot(path=str(path), full_page=False, type="jpeg", quality=70, timeout=min(settings.playwright_step_timeout_ms, 10000))
            self.screenshot_paths.append(str(path))
            logger.info("jobserve_apply_screenshot_saved path=%s", path)
        except Exception as exc:  # noqa: BLE001
            self.step(f"screenshot_failed_{name}", error=str(exc))

    def html(self, name: str, target=None) -> str | None:
        if not self.enabled:
            return None
        path = self.dir / f"{len(self.html_snapshot_paths) + 1:02d}_{_slug(name)}.html"
        try:
            html = (target or self.page).content()[:500_000]
            path.write_text(html, encoding="utf-8")
            self.html_snapshot_paths.append(str(path))
            logger.info("jobserve_apply_html_snapshot_saved path=%s", path)
            return str(path)
        except Exception as exc:  # noqa: BLE001
            self.step(f"html_snapshot_failed_{name}", error=str(exc))
            return None

    def result_kwargs(self, target=None) -> dict[str, Any]:
        target_page = target or self.page
        inventory = _inventory_browser(target_page, self.browser)
        return {
            "screenshot_paths": self.screenshot_paths,
            "screenshot_urls": [_artifact_url(path) for path in self.screenshot_paths],
            "html_snapshot_paths": self.html_snapshot_paths,
            "html_snapshot_urls": [_artifact_url(path) for path in self.html_snapshot_paths],
            "detected_buttons": inventory["buttons"],
            "detected_fields": inventory["fields"],
            "detected_selects": inventory["selects"],
            "detected_iframes": inventory["iframes"],
            "debug_steps": self.steps,
            "final_url": _safe_url(target_page),
            "final_error": self.final_error,
            "debug_mode": self.enabled,
        }


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")[:80] or "artifact"


def _artifact_url(path: str) -> str:
    try:
        relative = Path(path).resolve().relative_to(DEBUG_ARTIFACT_DIR.resolve())
    except Exception:  # noqa: BLE001
        return ""
    return f"/applications/debug-artifacts/{relative.as_posix()}"


def _exception_payload(stage: str, exc: BaseException, **extra: Any) -> dict[str, Any]:
    return {
        "stage": stage,
        "type": type(exc).__name__,
        "message": str(exc),
        "traceback": traceback.format_exc(),
        **extra,
    }


def _cv_upload_path(profile, diagnostics: dict[str, Any]) -> str | None:
    raw_path = getattr(profile, "cv_file_path", None) if profile is not None else None
    file_name = getattr(profile, "cv_file_name", None) if profile is not None else None
    blob = getattr(profile, "cv_file_bytes", None) if profile is not None else None
    diagnostics.update(
        {
            "stored_cv_file_path": raw_path,
            "stored_cv_file_name": file_name,
            "stored_cv_mime_type": getattr(profile, "cv_file_mime_type", None) if profile is not None else None,
            "stored_cv_file_size": getattr(profile, "cv_file_size", None) if profile is not None else None,
            "blob_present": bool(blob),
            "resolved_absolute_path": None,
            "path_exists": False,
            "path_readable": False,
            "path_file_size": None,
            "detected_mime_type": None,
            "materialized_from_blob": False,
            "set_input_files_succeeded": False,
        }
    )
    if raw_path:
        try:
            resolved = Path(raw_path).expanduser().resolve()
            diagnostics["resolved_absolute_path"] = str(resolved)
            diagnostics["path_exists"] = os.path.exists(resolved)
            diagnostics["path_readable"] = os.access(resolved, os.R_OK) if diagnostics["path_exists"] else False
            if diagnostics["path_exists"]:
                diagnostics["path_file_size"] = resolved.stat().st_size
                diagnostics["detected_mime_type"] = mimetypes.guess_type(str(resolved))[0]
                logger.info(
                    "jobserve_apply_cv_path_resolved path=%s exists=%s size=%s mime=%s",
                    resolved,
                    diagnostics["path_exists"],
                    diagnostics["path_file_size"],
                    diagnostics["detected_mime_type"],
                )
                return str(resolved)
        except Exception as exc:  # noqa: BLE001
            diagnostics["path_resolution_error"] = str(exc)
    if blob:
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", file_name or "cv.pdf").strip("._") or "cv.pdf"
        WORKER_CV_DIR.mkdir(parents=True, exist_ok=True)
        target = (WORKER_CV_DIR / f"{int(time.time() * 1000)}_{safe_name}").resolve()
        target.write_bytes(blob)
        diagnostics["resolved_absolute_path"] = str(target)
        diagnostics["path_exists"] = target.exists()
        diagnostics["path_readable"] = os.access(target, os.R_OK) if diagnostics["path_exists"] else False
        diagnostics["path_file_size"] = target.stat().st_size if target.exists() else None
        diagnostics["detected_mime_type"] = mimetypes.guess_type(str(target))[0]
        diagnostics["materialized_from_blob"] = True
        logger.info(
            "jobserve_apply_cv_materialized_from_db path=%s exists=%s size=%s mime=%s",
            target,
            diagnostics["path_exists"],
            diagnostics["path_file_size"],
            diagnostics["detected_mime_type"],
        )
        return str(target)
    return None


def _safe_url(page) -> str | None:
    try:
        return page.url
    except Exception:  # noqa: BLE001
        return None


def _safe_title(page) -> str | None:
    try:
        return page.title()
    except Exception:  # noqa: BLE001
        return None


def _page_state(page, browser) -> dict[str, Any]:
    return {
        "current_url": _safe_url(page),
        "page_title": _safe_title(page),
        "iframe_count": len(getattr(page, "frames", []) or []),
        "popup_window_count": len(getattr(page.context, "pages", []) or []) if getattr(page, "context", None) else None,
    }


def _latest_page(browser):
    try:
        pages = browser.contexts[0].pages
        return pages[-1] if pages else None
    except Exception:  # noqa: BLE001
        return None


def _context_name(context) -> str:
    try:
        return f"frame:{context.url}"
    except Exception:  # noqa: BLE001
        return "page"


def _all_contexts(page, browser) -> list[Any]:
    contexts: list[Any] = []
    try:
        for candidate_page in page.context.pages:
            contexts.append(candidate_page)
            contexts.extend(frame for frame in candidate_page.frames if frame is not candidate_page.main_frame)
    except Exception:  # noqa: BLE001
        contexts.append(page)
    return contexts


def _find_apply_target(page, browser):
    for context in _all_contexts(page, browser):
        locators = [
            context.get_by_role("button", name=re.compile(r"^apply(\s+now)?$", re.I)).first,
            context.get_by_role("link", name=re.compile(r"^apply(\s+now)?$", re.I)).first,
            context.locator("button, input[type=button], input[type=submit], a").filter(has_text=re.compile(r"^apply(\s+now)?$", re.I)).first,
            context.get_by_text(re.compile(r"^apply\b", re.I)).first,
        ]
        for locator in locators:
            try:
                if locator.count() and locator.is_visible(timeout=1000):
                    return locator
            except Exception:  # noqa: BLE001
                continue
    return None


def _find_jobserve_form_context(page, browser):
    deadline = time.time() + 12
    while time.time() < deadline:
        for context in _all_contexts(page, browser):
            try:
                has_modal = context.locator("[role=dialog], .modal, #ApplyModal").count() > 0
                has_job_application_text = context.get_by_text(re.compile(r"job application", re.I)).count() > 0
                has_fields = context.locator("input:not([type=hidden]), select, textarea").count() > 0
                if (has_modal or has_job_application_text) and has_fields:
                    return context
                if has_fields and context.locator("input[type=file], select").count() > 0:
                    return context
            except Exception:  # noqa: BLE001
                continue
        page.wait_for_timeout(500)
    return None


def _inventory_browser(page, browser) -> dict[str, list[dict[str, Any]]]:
    buttons: list[dict[str, Any]] = []
    fields: list[dict[str, Any]] = []
    selects: list[dict[str, Any]] = []
    iframes: list[dict[str, Any]] = []
    for context in _all_contexts(page, browser):
        inventory = _inventory_context(context)
        buttons.extend(inventory["buttons"])
        fields.extend(inventory["fields"])
        selects.extend(inventory["selects"])
        iframes.extend(inventory["iframes"])
    return {"buttons": buttons[:20], "fields": fields, "selects": selects, "iframes": iframes}


def _inventory_context(context) -> dict[str, list[dict[str, Any]]]:
    try:
        return context.evaluate(
            """() => {
                const visible = (el) => {
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style && style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
                };
                const labelFor = (el) => {
                    const id = el.getAttribute('id');
                    if (id) {
                        const label = document.querySelector(`label[for="${CSS.escape(id)}"]`);
                        if (label) return label.innerText.trim();
                    }
                    const parent = el.closest('label');
                    if (parent) return parent.innerText.trim();
                    return el.getAttribute('aria-label') || el.getAttribute('placeholder') || '';
                };
                const selectorish = (el) => {
                    const bits = [el.tagName.toLowerCase()];
                    if (el.id) bits.push(`#${el.id}`);
                    if (el.getAttribute('name')) bits.push(`[name="${el.getAttribute('name')}"]`);
                    if (el.className && typeof el.className === 'string') bits.push('.' + el.className.trim().split(/\\s+/).slice(0, 3).join('.'));
                    return bits.join('');
                };
                const buttons = [...document.querySelectorAll('button, a, input[type=button], input[type=submit]')]
                    .filter(visible)
                    .slice(0, 20)
                    .map((el) => ({
                        text: (el.innerText || el.value || el.getAttribute('aria-label') || '').trim(),
                        tag: el.tagName.toLowerCase(),
                        selector: selectorish(el),
                        href: el.href || null,
                        id: el.id || null,
                        name: el.getAttribute('name') || null,
                        type: el.getAttribute('type') || null
                    }));
                const fields = [...document.querySelectorAll('input, select, textarea')]
                    .filter(visible)
                    .map((el) => ({
                        tag: el.tagName.toLowerCase(),
                        type: el.getAttribute('type') || null,
                        id: el.id || null,
                        name: el.getAttribute('name') || null,
                        placeholder: el.getAttribute('placeholder') || null,
                        label: labelFor(el),
                        value: el.value || '',
                        selector: selectorish(el),
                        required: Boolean(el.required),
                        options: el.tagName.toLowerCase() === 'select' ? [...el.options].map((option) => option.text.trim()).filter(Boolean).slice(0, 50) : []
                    }));
                const selects = fields.filter((field) => field.tag === 'select');
                const file_inputs = fields.filter((field) => field.type === 'file');
                const iframes = [...document.querySelectorAll('iframe')]
                    .map((el) => ({ id: el.id || null, name: el.name || null, src: el.src || null, title: el.title || null }));
                return { buttons, fields, selects, file_inputs, iframes };
            }"""
        )
    except Exception as exc:  # noqa: BLE001
        return {"buttons": [], "fields": [], "selects": [], "file_inputs": [], "iframes": [{"error": str(exc), "context": _context_name(context)}]}


def _detect_jobserve_dropdowns(selects: list[dict[str, Any]]) -> dict[str, bool]:
    result: dict[str, bool] = {}
    for key, patterns in JOBSERVE_REQUIRED_DROPDOWN_PATTERNS.items():
        result[key] = any(
            any(re.search(pattern, " ".join(str(select.get(part) or "") for part in ["label", "name", "id", "placeholder"]), re.I) for pattern in patterns)
            for select in selects
        )
    return result


def _run_jobserve_search_to_apply(
    page,
    browser,
    candidates: dict[str, FieldCandidate],
    profile,
    job_context: dict[str, Any],
    *,
    mode: str,
    keep_open_for_review: bool,
    debug_mode: bool = False,
    profile_diagnostics: dict[str, Any] | None = None,
    progress_callback: Callable[[str, dict[str, Any]], None] | None = None,
) -> AssistApplyResult:
    flow_started = time.perf_counter()
    timing_diagnostics: dict[str, Any] = {}
    flow: dict[str, Any] = {
        "mode": "search_to_apply",
        "search_url": JOBSERVE_SEARCH_URL,
        "search_defaults": _jobserve_search_preferences(profile),
        "target": job_context,
        "search_page_loaded": False,
        "search_controls": {},
        "search_button_clicked": False,
        "target_job_match_candidates": [],
        "selected_job": None,
        "apply_button_clicked": False,
        "modal_opened": False,
        "email_filled": False,
        "uk_status_selected": False,
        "cv_upload_succeeded": False,
        "final_apply_clicked": False,
        "submitted_confirmation_detected": False,
        "account_toggles_turned_off": [],
        "modal_closed": False,
        "fallback_used": False,
    }
    profile_diagnostics = profile_diagnostics or {"profile_loaded": profile is not None, "loaded_profile_values": {}, "candidate_keys": sorted(candidates.keys()), "mapped_fields": {}}
    upload_diagnostics: dict[str, Any] = {}
    select_diagnostics: list[dict[str, Any]] = []
    exceptions: list[dict[str, Any]] = []
    filled: list[str] = []
    unfilled: list[str] = []
    unfilled_required: list[str] = []
    warnings: list[str] = []
    uploaded_cv = False
    submitted = False
    status = "review_required"
    debug = _ApplyDebugRecorder(page, browser, enabled=debug_mode, prefix="jobserve_search", progress_callback=progress_callback)

    search_started = time.perf_counter()
    _retry_step("search page load", lambda: page.goto(JOBSERVE_SEARCH_URL, wait_until="domcontentloaded", timeout=settings.page_navigation_timeout_ms))
    timing_diagnostics["search_page_load_ms"] = int((time.perf_counter() - search_started) * 1000)
    flow["search_page_loaded"] = True
    debug.step("jobserve_search_page_loaded", jobserve_flow_diagnostics=flow)
    debug.screenshot("search_page_loaded")

    if not _fill_jobserve_search_form(page, flow, select_diagnostics, step_callback=progress_callback):
        debug.final_error = "JobServe search form could not be filled."
        debug.html("search_form_failed", page)
        raise RuntimeError(debug.final_error)
    debug.step("jobserve_search_form_filled", jobserve_flow_diagnostics=flow)

    if not _click_jobserve_search(page):
        debug.final_error = "JobServe search button could not be clicked."
        debug.html("search_form_failed", page)
        raise RuntimeError(debug.final_error)
    flow["search_button_clicked"] = True
    page.wait_for_load_state("domcontentloaded", timeout=20000)
    page.wait_for_timeout(1200)
    debug.step("jobserve_search_submitted", jobserve_flow_diagnostics=flow)
    debug.screenshot("search_results_loaded")

    match_started = time.perf_counter()
    selected = _select_jobserve_result(page, job_context, flow)
    timing_diagnostics["result_matching_ms"] = int((time.perf_counter() - match_started) * 1000)
    if selected is None:
        debug.final_error = "No matching JobServe search result found."
        debug.html("no_matching_job_found", page)
        raise RuntimeError(debug.final_error)
    flow["selected_job"] = selected
    debug.step("jobserve_target_job_selected", jobserve_flow_diagnostics=flow)

    page.wait_for_timeout(1200)
    debug.screenshot("job_details_loaded")
    apply_target = _find_apply_target(page, browser)
    if apply_target is None:
        debug.final_error = "Apply button missing after selecting JobServe search result."
        debug.html("apply_button_missing", page)
        raise RuntimeError(debug.final_error)
    modal_started = time.perf_counter()
    _retry_step("apply button click", lambda: apply_target.click(timeout=settings.playwright_step_timeout_ms))
    flow["apply_button_clicked"] = True
    page.wait_for_timeout(1500)
    context = _retry_step("modal open", lambda: _find_jobserve_form_context(page, browser))
    timing_diagnostics["modal_open_ms"] = int((time.perf_counter() - modal_started) * 1000)
    flow["modal_opened"] = context is not None
    debug.step("jobserve_apply_modal_wait_complete", jobserve_flow_diagnostics=flow, target_context=_context_name(context) if context else None)
    debug.screenshot("apply_modal_wait_complete")
    if context is None:
        debug.final_error = "JobServe application modal missing."
        debug.html("modal_missing", page)
        raise RuntimeError(debug.final_error)

    fill_result = _fill_jobserve_application_form(
        context,
        page,
        candidates,
        profile,
        mode=mode,
        flow=flow,
        filled=filled,
        unfilled=unfilled,
        unfilled_required=unfilled_required,
        warnings=warnings,
        upload_diagnostics=upload_diagnostics,
        select_diagnostics=select_diagnostics,
        profile_diagnostics=profile_diagnostics,
        exceptions=exceptions,
        debug=debug,
        step_callback=progress_callback,
    )
    uploaded_cv = fill_result["uploaded_cv"]
    timing_diagnostics.update(fill_result.get("timing_diagnostics", {}))
    debug.step("jobserve_application_form_filled", jobserve_flow_diagnostics=flow, filled_fields=_dedupe(filled), unfilled_required_fields=_dedupe(unfilled_required))
    debug.screenshot("after_application_form_fill")

    if mode == "submit_with_confirmation":
        if unfilled_required:
            debug.final_error = f"Required fields missing: {', '.join(_dedupe(unfilled_required))}"
            debug.html("required_fields_missing", context)
            raise RuntimeError(debug.final_error)
        apply_button = _jobserve_apply_button(context)
        if apply_button.count() == 0:
            debug.final_error = "Submit button not found in JobServe apply form."
            debug.html("submit_button_not_found", context)
            raise RuntimeError(debug.final_error)
        submit_started = time.perf_counter()
        apply_button.click(timeout=settings.playwright_step_timeout_ms)
        flow["final_apply_clicked"] = True
        debug.step("jobserve_final_apply_clicked", jobserve_flow_diagnostics=flow)
        success = page.get_by_text("Your application has been submitted.").first
        try:
            success.wait_for(timeout=settings.page_navigation_timeout_ms)
            timing_diagnostics["submit_wait_ms"] = int((time.perf_counter() - submit_started) * 1000)
            flow["submitted_confirmation_detected"] = True
            submitted = True
            status = "submitted"
        except Exception as exc:  # noqa: BLE001
            debug.final_error = "JobServe submission confirmation not detected."
            exceptions.append(_exception_payload("confirmation_detection", exc, jobserve_flow_diagnostics=dict(flow)))
            debug.html("confirmation_not_detected", page)
            raise RuntimeError(debug.final_error) from exc
        flow["account_toggles_turned_off"] = _disable_jobserve_account_options(context, warnings)
        flow["modal_closed"] = _close_modal(page)
    else:
        warnings.append("Review-only mode: JobServe search-to-apply flow stopped before final Apply.")
        if keep_open_for_review:
            warnings.append("Browser left open for manual review; close it after reviewing the application.")

    return AssistApplyResult(
        status=status,
        filled_fields=_dedupe(filled),
        unfilled_fields=_dedupe(unfilled),
        unfilled_required_fields=_dedupe(unfilled_required),
        uploaded_cv=uploaded_cv,
        submitted=submitted,
        warnings=_dedupe(warnings),
        screenshot_path=debug.screenshot_paths[-1] if debug.screenshot_paths else None,
        profile_diagnostics=profile_diagnostics,
        jobserve_flow_diagnostics=flow,
        timing_diagnostics={**timing_diagnostics, "total_runtime_ms": int((time.perf_counter() - flow_started) * 1000)},
        progress={"current_step": "submitted" if submitted else "review_required", "elapsed_ms": int((time.perf_counter() - flow_started) * 1000), "last_heartbeat_at": utcnow().isoformat()},
        upload_diagnostics=upload_diagnostics,
        select_diagnostics=select_diagnostics,
        exceptions=exceptions,
        **debug.result_kwargs(page),
    )


def _jobserve_search_preferences(profile) -> dict[str, str]:
    preferences = getattr(profile, "preferences", None) or {}
    return {
        "keywords": str(preferences.get("jobserve_search_keywords") or JOBSERVE_DEFAULTS["search_keywords"]),
        "location": str(preferences.get("jobserve_search_location") or JOBSERVE_DEFAULTS["search_location"]),
        "distance": str(preferences.get("jobserve_search_distance") or JOBSERVE_DEFAULTS["search_distance"]),
        "posted_within": str(preferences.get("jobserve_posted_within") or JOBSERVE_DEFAULTS["posted_within"]),
        "job_type": str(preferences.get("jobserve_job_type") or JOBSERVE_DEFAULTS["job_type"]),
        "working_status": str(getattr(profile, "work_status_uk", None) or preferences.get("jobserve_working_status") or JOBSERVE_DEFAULTS["working_status"]),
    }


def _fill_jobserve_search_form(
    page,
    flow: dict[str, Any],
    select_diagnostics: list[dict[str, Any]],
    step_callback: Callable[[str, dict[str, Any]], None] | None = None,
) -> bool:
    prefs = flow["search_defaults"]
    controls = flow["search_controls"]
    controls["keywords"] = _fill_first_label_or_selector(page, [r"keywords?", r"what"], ['input[name*="keyword" i]', 'input[id*="keyword" i]', "input[type=search]"], prefs["keywords"])
    _report_jobserve_step(step_callback, "jobserve_search_keyword_filled", value=prefs["keywords"], succeeded=controls["keywords"])
    controls["location"] = _fill_first_label_or_selector(page, [r"location", r"where"], ['input[name*="location" i]', 'input[id*="location" i]'], prefs["location"])
    _report_jobserve_step(step_callback, "jobserve_search_location_filled", value=prefs["location"], succeeded=controls["location"])
    controls["distance"] = _select_first_label_or_selector(page, [r"distance", r"miles"], ['select[name*="distance" i]', 'select[id*="distance" i]'], prefs["distance"], "Search distance", select_diagnostics)
    _report_jobserve_step(step_callback, "jobserve_search_distance_selected", value=prefs["distance"], succeeded=controls["distance"])
    controls["posted_within"] = _select_first_label_or_selector(page, [r"posted", r"date"], ['select[name*="posted" i]', 'select[id*="posted" i]', 'select[name*="age" i]'], prefs["posted_within"], "Posted within", select_diagnostics)
    _report_jobserve_step(step_callback, "jobserve_search_posted_selected", value=prefs["posted_within"], succeeded=controls["posted_within"])
    controls["job_type"] = _select_first_label_or_selector(page, [r"job type", r"type"], ['select[name*="type" i]', 'select[id*="type" i]'], prefs["job_type"], "Job type", select_diagnostics)
    _report_jobserve_step(step_callback, "jobserve_search_job_type_selected", value=prefs["job_type"], succeeded=controls["job_type"])
    controls["remote_only_unchecked"] = _set_checkbox_by_label(page, [r"remote only"], checked=False)
    _report_jobserve_step(step_callback, "jobserve_search_remote_only_unchecked", succeeded=controls["remote_only_unchecked"])
    controls["industries_select_all"] = _select_all_jobserve_industries(page)
    _report_jobserve_step(step_callback, "jobserve_search_industries_selected", value="Select All", succeeded=controls["industries_select_all"])
    return bool(controls["keywords"] and controls["location"])


def _report_jobserve_step(step_callback: Callable[[str, dict[str, Any]], None] | None, step: str, **payload: Any) -> None:
    if step_callback is not None:
        step_callback(step, payload)


def _fill_first_label_or_selector(page, labels: list[str], selectors: list[str], value: str) -> bool:
    for pattern in labels:
        try:
            locator = page.get_by_label(re.compile(pattern, re.I)).first
            locator.fill(value, timeout=1500)
            return True
        except Exception:  # noqa: BLE001
            continue
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            locator.fill(value, timeout=1500)
            return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _select_first_label_or_selector(page, labels: list[str], selectors: list[str], value: str, field_name: str, diagnostics: list[dict[str, Any]]) -> bool:
    for pattern in labels:
        try:
            locator = page.get_by_label(re.compile(pattern, re.I)).first
            if _select_locator_option(locator, value, field_name=field_name, label_pattern=pattern, diagnostics=diagnostics):
                return True
        except Exception:  # noqa: BLE001
            continue
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if _select_locator_option(locator, value, field_name=field_name, label_pattern=selector, diagnostics=diagnostics):
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _set_checkbox_by_label(page, labels: list[str], *, checked: bool) -> bool:
    for pattern in labels:
        for control in page.get_by_label(re.compile(pattern, re.I)).all():
            try:
                current = control.is_checked(timeout=500)
                if current != checked:
                    control.set_checked(checked, timeout=1000)
                return True
            except Exception:  # noqa: BLE001
                continue
    return False


def _select_all_jobserve_industries(page) -> bool:
    for locator in [
        page.get_by_text(re.compile(r"industr(y|ies)", re.I)).first,
        page.locator('select[name*="industry" i], select[id*="industry" i]').first,
    ]:
        try:
            locator.click(timeout=1000)
            break
        except Exception:  # noqa: BLE001
            continue
    for text in [r"select all", r"all industries"]:
        try:
            page.get_by_text(re.compile(text, re.I)).first.click(timeout=1500)
            return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _click_jobserve_search(page) -> bool:
    for locator in [
        page.get_by_role("button", name=re.compile(r"search", re.I)).first,
        page.locator("input[type=submit], button[type=submit]").first,
        page.get_by_text(re.compile(r"^search$", re.I)).first,
    ]:
        try:
            locator.click(timeout=3000)
            return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _select_jobserve_result(page, job_context: dict[str, Any], flow: dict[str, Any]) -> dict[str, Any] | None:
    candidates = _jobserve_result_candidates(page)
    ranked = _rank_jobserve_candidates(candidates, job_context)
    if ranked and not _jobserve_target_has_identity(job_context):
        ranked[0]["score"] = max(int(ranked[0].get("score") or 0), 1)
    flow["target_job_match_candidates"] = ranked[:10]
    if not ranked or ranked[0]["score"] <= 0:
        return None
    selected = ranked[0]
    href = selected.get("href")
    try:
        if href:
            page.locator(f'a[href="{href}"]').first.click(timeout=5000)
        else:
            page.get_by_text(str(selected.get("title") or selected.get("text") or ""), exact=False).first.click(timeout=5000)
        return selected
    except Exception:  # noqa: BLE001
        return None


def _jobserve_target_has_identity(target: dict[str, Any]) -> bool:
    return any(str(target.get(key) or "").strip() for key in ["source_job_id", "original_external_id", "title", "original_title", "company_name", "original_company"])


def _jobserve_result_candidates(page) -> list[dict[str, Any]]:
    try:
        return page.evaluate(
            """() => Array.from(document.querySelectorAll('a[href], article, .job, .job-result, .JobResult, [data-jobid]'))
                .map((el) => ({
                    text: (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' '),
                    href: el.href || el.querySelector?.('a[href]')?.href || '',
                    title: (el.querySelector?.('h1,h2,h3,.job-title,.title')?.innerText || '').trim(),
                    company: (el.querySelector?.('.company,.recruiter,.employer')?.innerText || '').trim(),
                    reference: el.getAttribute('data-jobid') || ''
                }))
                .filter((item) => item.text || item.href)
                .slice(0, 80)"""
        )
    except Exception:  # noqa: BLE001
        return []


def _rank_jobserve_candidates(candidates: list[dict[str, Any]], target: dict[str, Any]) -> list[dict[str, Any]]:
    ranked = []
    for candidate in candidates:
        item = dict(candidate)
        item["score"] = _jobserve_match_score(candidate, target)
        ranked.append(item)
    return sorted(ranked, key=lambda item: item["score"], reverse=True)


def _jobserve_match_score(candidate: dict[str, Any], target: dict[str, Any]) -> int:
    haystack = _normalize_select_text(" ".join(str(candidate.get(key) or "") for key in ["text", "href", "title", "company", "reference"]))
    score = 0
    for key, weight in [("source_job_id", 50), ("original_external_id", 50), ("title", 30), ("original_title", 30), ("company_name", 20), ("original_company", 20)]:
        value = _normalize_select_text(str(target.get(key) or ""))
        if value and value in haystack:
            score += weight
    return score


def _fill_jobserve_application_form(
    context,
    target_page,
    candidates: dict[str, FieldCandidate],
    profile,
    *,
    mode: str,
    flow: dict[str, Any],
    filled: list[str],
    unfilled: list[str],
    unfilled_required: list[str],
    warnings: list[str],
    upload_diagnostics: dict[str, Any],
    select_diagnostics: list[dict[str, Any]],
    profile_diagnostics: dict[str, Any],
    exceptions: list[dict[str, Any]],
    debug: _ApplyDebugRecorder,
    step_callback: Callable[[str, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    form_inventory = _inventory_context(context)
    profile_diagnostics.setdefault("mapped_fields", {})
    email = candidates.get("email")
    email_label = _fill_by_label_patterns(context, [r"email address", r"email"], email)
    if email_label:
        filled.append("Email Address")
        flow["email_filled"] = True
        profile_diagnostics["mapped_fields"]["email"] = {"mapped": True, "label": email_label}
        _report_jobserve_step(step_callback, "jobserve_apply_email_filled", succeeded=True, label=email_label)
    else:
        unfilled_required.append("Email Address")
        profile_diagnostics["mapped_fields"]["email"] = {"mapped": False, "reason": "email field missing or profile email missing"}
        _report_jobserve_step(step_callback, "jobserve_apply_email_filled", succeeded=False)
        debug.html("email_field_missing", context)

    _ensure_confirmation_email_checked(context, flow)
    _report_jobserve_step(step_callback, "jobserve_apply_confirmation_email_checked", succeeded=flow.get("confirmation_email_checked"))

    working_status = candidates.get("work_authorization")
    if working_status and _select_work_status(context, working_status.value, select_diagnostics):
        filled.append("Working status in UK")
        flow["uk_status_selected"] = True
        profile_diagnostics["mapped_fields"]["work_authorization"] = {"mapped": True, "label": "Working status in UK", "value": working_status.value}
        _report_jobserve_step(step_callback, "jobserve_apply_working_status_selected", succeeded=True, value=working_status.value)
    else:
        unfilled_required.append("Working status in UK")
        profile_diagnostics["mapped_fields"]["work_authorization"] = {"mapped": False, "reason": "working status dropdown missing or configured value missing"}
        exceptions.append({"stage": "working_status", "type": "SelectOptionError", "message": "Working status dropdown missing or could not be selected", "traceback": None})
        _report_jobserve_step(step_callback, "jobserve_apply_working_status_selected", succeeded=False, value=working_status.value if working_status else None)
        debug.html("working_status_dropdown_missing", context)

    _handle_optional_dropdown_if_present(
        context,
        form_inventory["selects"],
        candidates,
        "availability_notice",
        [r"availability", r"notice"],
        lambda value: value,
        "Availability notice",
        filled,
        unfilled,
        select_diagnostics,
        profile_diagnostics,
    )
    _handle_optional_dropdown_if_present(
        context,
        form_inventory["selects"],
        candidates,
        "salary_expectation_gbp",
        [r"salary expectation", r"salary"],
        salary_range_label,
        "Salary expectation",
        filled,
        unfilled,
        select_diagnostics,
        profile_diagnostics,
    )
    _handle_optional_dropdown_if_present(
        context,
        form_inventory["selects"],
        candidates,
        "travel_distance_miles",
        [r"travel distance", r"travel"],
        travel_distance_label,
        "Travel distance",
        filled,
        unfilled,
        select_diagnostics,
        profile_diagnostics,
    )

    upload_diagnostics["detected_file_inputs"] = form_inventory["file_inputs"]
    upload_diagnostics["file_input_detected"] = bool(context.locator("input[type=file], #filCV").count())
    cv_path = _cv_upload_path(profile, upload_diagnostics)
    debug.step("before_cv_upload", upload_diagnostics=upload_diagnostics)
    uploaded_cv = False
    upload_started = time.perf_counter()
    if cv_path and upload_diagnostics.get("path_exists") and upload_diagnostics["file_input_detected"]:
        try:
            file_input = context.locator("input[type=file], #filCV, input#filCV").first
            _retry_step("cv upload", lambda: file_input.set_input_files(cv_path, timeout=settings.playwright_step_timeout_ms))
            uploaded_cv = True
            filled.append("CV upload")
            upload_diagnostics["set_input_files_succeeded"] = True
            upload_diagnostics["displayed_file_name"] = _uploaded_cv_display_name(context, Path(cv_path).name)
            flow["cv_upload_succeeded"] = True
            _report_jobserve_step(step_callback, "jobserve_apply_cv_uploaded", succeeded=True, path=cv_path)
        except Exception as exc:  # noqa: BLE001
            payload = _exception_payload("cv_upload", exc, upload_diagnostics=dict(upload_diagnostics))
            exceptions.append(payload)
            upload_diagnostics["set_input_files_succeeded"] = False
            upload_diagnostics["set_input_files_error"] = payload
            unfilled_required.append("CV upload")
            warnings.append(f"Could not upload CV: {exc}")
            _report_jobserve_step(step_callback, "jobserve_apply_cv_uploaded", succeeded=False, path=cv_path, error=str(exc))
            debug.html("cv_upload_failed", context)
    else:
        upload_diagnostics["failure_reason"] = "CV file input missing or worker-accessible CV path unavailable."
        exceptions.append({"stage": "cv_upload_preflight", "type": "FileNotFoundError", "message": upload_diagnostics["failure_reason"], "traceback": None, "upload_diagnostics": dict(upload_diagnostics)})
        unfilled_required.append("CV upload")
        _report_jobserve_step(step_callback, "jobserve_apply_cv_uploaded", succeeded=False, path=cv_path, error=upload_diagnostics["failure_reason"])
        debug.html("cv_upload_failed", context)
    debug.screenshot("after_cv_upload_attempt")
    timing_diagnostics = {"cv_upload_ms": int((time.perf_counter() - upload_started) * 1000)}

    if mode == "review_only":
        return {"uploaded_cv": uploaded_cv, "timing_diagnostics": timing_diagnostics}

    return {"uploaded_cv": uploaded_cv, "timing_diagnostics": timing_diagnostics}


def _retry_step(name: str, action: Callable[[], Any], *, attempts: int = 2, delay_ms: int = 1000) -> Any:
    last_exc: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return action()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            logger.warning("jobserve_step_retryable_failure step=%s attempt=%s attempts=%s error=%s", name, attempt, attempts, exc)
            if attempt < attempts:
                time.sleep(delay_ms / 1000)
    if last_exc is not None:
        raise last_exc
    return None


def _handle_optional_dropdown_if_present(
    page,
    selects: list[dict[str, Any]],
    candidates: dict[str, FieldCandidate],
    key: str,
    label_patterns: list[str],
    matcher,
    field_name: str,
    filled: list[str],
    unfilled: list[str],
    select_diagnostics: list[dict[str, Any]],
    profile_diagnostics: dict[str, Any],
) -> None:
    present = _detect_jobserve_dropdowns(selects).get(key, False)
    if not present:
        profile_diagnostics.setdefault("mapped_fields", {})[key] = {"mapped": False, "reason": "field not present"}
        return
    candidate = candidates.get(key)
    if candidate is None:
        unfilled.append(field_name)
        profile_diagnostics.setdefault("mapped_fields", {})[key] = {"mapped": False, "reason": "field present but profile value missing"}
        return
    option = matcher(candidate.value)
    if option and _select_dropdown_by_label_patterns(page, label_patterns, option, field_name=field_name, diagnostics=select_diagnostics):
        filled.append(field_name)
        profile_diagnostics.setdefault("mapped_fields", {})[key] = {"mapped": True, "label": field_name, "target_option": option}
        return
    unfilled.append(field_name)
    profile_diagnostics.setdefault("mapped_fields", {})[key] = {"mapped": False, "reason": "field present but selection failed", "value": candidate.value}


def _ensure_confirmation_email_checked(page, flow: dict[str, Any]) -> None:
    flow["confirmation_email_checked"] = _set_checkbox_by_label(page, [r"send confirmation.*email", r"confirmation.*email"], checked=True)


def _uploaded_cv_display_name(page, file_name: str) -> str | None:
    try:
        text = page.locator("body").inner_text(timeout=1000)
    except Exception:  # noqa: BLE001
        return None
    return file_name if file_name in text else None


def _run_jobserve_modal(
    page,
    browser,
    candidates: dict[str, FieldCandidate],
    profile,
    *,
    mode: str,
    keep_open_for_review: bool,
    debug_mode: bool = False,
    profile_diagnostics: dict[str, Any] | None = None,
    progress_callback: Callable[[str, dict[str, Any]], None] | None = None,
) -> AssistApplyResult:
    flow_started = time.perf_counter()
    filled: list[str] = []
    unfilled_required: list[str] = []
    unfilled: list[str] = []
    warnings: list[str] = []
    uploaded_cv = False
    submitted = False
    status = "review_required"
    profile_diagnostics = profile_diagnostics or {"profile_loaded": profile is not None, "loaded_profile_values": {}, "candidate_keys": sorted(candidates.keys()), "mapped_fields": {}}
    upload_diagnostics: dict[str, Any] = {}
    select_diagnostics: list[dict[str, Any]] = []
    exceptions: list[dict[str, Any]] = []
    debug = _ApplyDebugRecorder(page, browser, enabled=debug_mode, progress_callback=progress_callback)
    debug.step("initial_page_loaded")
    debug.screenshot("initial_page_loaded")

    apply_target = _find_apply_target(page, browser)
    debug.step("apply_button_lookup", apply_button_found=apply_target is not None, detected_buttons=_inventory_browser(page, browser)["buttons"][:20])
    if apply_target is None:
        debug.final_error = "No visible JobServe Apply button/link found."
        debug.html("no_apply_button_found")
        return AssistApplyResult(
            status="review_required",
            filled_fields=[],
            unfilled_fields=[],
            unfilled_required_fields=[],
            uploaded_cv=False,
            submitted=False,
            warnings=_dedupe([*warnings, debug.final_error]),
            screenshot_path=debug.screenshot_paths[-1] if debug.screenshot_paths else None,
            profile_diagnostics=profile_diagnostics,
            timing_diagnostics={"total_runtime_ms": int((time.perf_counter() - flow_started) * 1000)},
            upload_diagnostics=upload_diagnostics,
            select_diagnostics=select_diagnostics,
            exceptions=exceptions,
            **debug.result_kwargs(),
        )

    pages_before = len(page.context.pages)
    apply_target.click(timeout=8000)
    page.wait_for_timeout(1000)
    target_page = _latest_page(browser) or page
    if len(page.context.pages) <= pages_before:
        target_page = page
    else:
        target_page.wait_for_load_state("domcontentloaded", timeout=15000)
    debug.page = target_page
    debug.step("apply_button_clicked", apply_button_clicked=True, popup_opened=target_page is not page)

    target_page.wait_for_timeout(3500 if debug_mode else 1200)
    debug.screenshot("after_clicking_apply")
    context = _find_jobserve_form_context(target_page, browser)
    debug.step(
        "modal_wait_complete",
        modal_found=context is not None,
        job_application_modal_found=context is not None,
        target_context=_context_name(context) if context else None,
    )
    debug.screenshot("after_modal_wait")
    if context is None:
        debug.final_error = "Job Application modal/form not found after clicking Apply."
        debug.html("modal_not_found", target_page)
        return AssistApplyResult(
            status="review_required",
            filled_fields=[],
            unfilled_fields=[],
            unfilled_required_fields=[],
            uploaded_cv=False,
            submitted=False,
            warnings=_dedupe([*warnings, debug.final_error]),
            screenshot_path=debug.screenshot_paths[-1] if debug.screenshot_paths else None,
            profile_diagnostics=profile_diagnostics,
            timing_diagnostics={"total_runtime_ms": int((time.perf_counter() - flow_started) * 1000)},
            upload_diagnostics=upload_diagnostics,
            select_diagnostics=select_diagnostics,
            exceptions=exceptions,
            **debug.result_kwargs(target_page),
        )

    form_inventory = _inventory_context(context)
    debug.step(
        "before_filling",
        form_fields_detected=len(form_inventory["fields"]),
        cv_upload_input_detected=bool(form_inventory["file_inputs"]),
        required_dropdowns_detected=_detect_jobserve_dropdowns(form_inventory["selects"]),
        detected_fields=form_inventory["fields"],
        detected_selects=form_inventory["selects"],
    )
    debug.screenshot("before_filling")
    if not form_inventory["fields"]:
        debug.final_error = "No visible application fields detected in JobServe apply form."
        debug.html("no_fields_detected", context)
        return AssistApplyResult(
            status="review_required",
            filled_fields=[],
            unfilled_fields=[],
            unfilled_required_fields=[],
            uploaded_cv=False,
            submitted=False,
            warnings=_dedupe([*warnings, debug.final_error]),
            screenshot_path=debug.screenshot_paths[-1] if debug.screenshot_paths else None,
            profile_diagnostics=profile_diagnostics,
            timing_diagnostics={"total_runtime_ms": int((time.perf_counter() - flow_started) * 1000)},
            upload_diagnostics=upload_diagnostics,
            select_diagnostics=select_diagnostics,
            exceptions=exceptions,
            **debug.result_kwargs(target_page),
        )

    if _captcha_visible(target_page):
        warnings.append("Captcha detected; manual review required.")

    _disable_jobserve_account_options(context, warnings)
    required = ["email"]
    for key, patterns in {
        "email": [r"email address", r"email"],
        "first_name": [r"first name"],
        "last_name": [r"last name", r"surname"],
        "phone": [r"phone", r"mobile"],
    }.items():
        label = _fill_by_label_patterns(context, patterns, candidates.get(key))
        if label:
            filled.append(label)
            profile_diagnostics.setdefault("mapped_fields", {})[key] = {"mapped": True, "label": label}
        elif key in required:
            unfilled_required.append(key.replace("_", " "))
            profile_diagnostics.setdefault("mapped_fields", {})[key] = {"mapped": False, "reason": "field not found or candidate missing"}

    if candidates.get("work_authorization"):
        if _select_work_status(context, candidates["work_authorization"].value, select_diagnostics):
            filled.append("Working status in UK")
            profile_diagnostics.setdefault("mapped_fields", {})["work_authorization"] = {"mapped": True, "label": "Working status in UK"}
        else:
            unfilled.append("Working status in UK")
            profile_diagnostics.setdefault("mapped_fields", {})["work_authorization"] = {"mapped": False, "reason": "select/fill failed"}
            exceptions.append(
                {
                    "stage": "select",
                    "type": "SelectOptionError",
                    "message": "Could not fill working status in UK",
                    "traceback": None,
                    "field": "Working status in UK",
                    "select_diagnostic": select_diagnostics[-1] if select_diagnostics else None,
                }
            )
    else:
        unfilled_required.append("working status in UK")
        profile_diagnostics.setdefault("mapped_fields", {})["work_authorization"] = {"mapped": False, "reason": "candidate missing"}

    _handle_required_dropdown(
        context,
        candidates,
        "availability_notice",
        [r"availability", r"notice"],
        lambda value: value,
        "Availability notice",
        "Availability notice missing",
        filled,
        unfilled_required,
        warnings,
        select_diagnostics,
        profile_diagnostics,
        exceptions,
    )
    _handle_required_dropdown(
        context,
        candidates,
        "salary_expectation_gbp",
        [r"salary expectation", r"salary"],
        salary_range_label,
        "Salary expectation",
        "Salary expectation missing",
        filled,
        unfilled_required,
        warnings,
        select_diagnostics,
        profile_diagnostics,
        exceptions,
        no_match_warning="Could not match salary range",
    )
    _handle_required_dropdown(
        context,
        candidates,
        "travel_distance_miles",
        [r"travel distance", r"travel"],
        travel_distance_label,
        "Travel distance",
        "Travel distance missing",
        filled,
        unfilled_required,
        warnings,
        select_diagnostics,
        profile_diagnostics,
        exceptions,
    )

    upload_diagnostics["detected_file_inputs"] = form_inventory["file_inputs"]
    upload_diagnostics["file_input_detected"] = bool(context.locator("input[type=file]").count())
    cv_path = _cv_upload_path(profile, upload_diagnostics)
    debug.step("before_cv_upload", upload_diagnostics=upload_diagnostics)
    if not upload_diagnostics["file_input_detected"]:
        debug.final_error = debug.final_error or "CV upload input not found in JobServe apply form."
        debug.html("cv_upload_input_not_found", context)
    if cv_path and upload_diagnostics.get("path_exists"):
        try:
            file_input = context.locator("input[type=file], #filCV").first
            file_input.set_input_files(cv_path, timeout=5000)
            upload_diagnostics["set_input_files_succeeded"] = True
            uploaded_cv = True
            filled.append("CV upload")
            logger.info(
                "jobserve_apply_cv_upload_succeeded path=%s exists=%s size=%s mime=%s",
                upload_diagnostics.get("resolved_absolute_path"),
                upload_diagnostics.get("path_exists"),
                upload_diagnostics.get("path_file_size"),
                upload_diagnostics.get("detected_mime_type"),
            )
        except Exception as exc:  # noqa: BLE001
            payload = _exception_payload("cv_upload", exc, upload_diagnostics=dict(upload_diagnostics))
            exceptions.append(payload)
            upload_diagnostics["set_input_files_succeeded"] = False
            upload_diagnostics["set_input_files_error"] = payload
            warnings.append(f"Could not upload CV: {exc}")
            unfilled_required.append("CV upload")
            debug.final_error = debug.final_error or "CV upload input not found or could not be populated."
            logger.exception(
                "jobserve_apply_cv_upload_failed path=%s exists=%s size=%s mime=%s",
                upload_diagnostics.get("resolved_absolute_path"),
                upload_diagnostics.get("path_exists"),
                upload_diagnostics.get("path_file_size"),
                upload_diagnostics.get("detected_mime_type"),
            )
            debug.html("cv_upload_failed", context)
    else:
        upload_diagnostics["failure_reason"] = "No worker-accessible CV file path could be resolved." if cv_path is None else "Resolved CV path does not exist."
        exceptions.append(
            {
                "stage": "cv_upload_preflight",
                "type": "FileNotFoundError",
                "message": upload_diagnostics["failure_reason"],
                "traceback": None,
                "upload_diagnostics": dict(upload_diagnostics),
            }
        )
        unfilled_required.append("CV upload")
    debug.screenshot("after_cv_upload_attempt")

    debug.step(
        "after_filling",
        filled_fields=_dedupe(filled),
        unfilled_fields=_dedupe(unfilled),
        unfilled_required_fields=_dedupe(unfilled_required),
        uploaded_cv=uploaded_cv,
    )
    debug.screenshot("after_filling")
    _disable_jobserve_account_options(context, warnings)
    if mode == "submit_with_confirmation":
        debug.screenshot("before_submit")
        if unfilled_required:
            raise RuntimeError(f"Required fields missing: {', '.join(_dedupe(unfilled_required))}")
        apply_button = _jobserve_apply_button(context)
        if apply_button.count() == 0:
            debug.final_error = "Submit button not found in JobServe apply form."
            debug.html("submit_button_not_found", context)
            raise RuntimeError(debug.final_error)
        apply_button.click(timeout=8000)
        success = target_page.get_by_text("Your application has been submitted.").first
        success.wait_for(timeout=12000)
        submitted = True
        status = "submitted"
        _close_modal(target_page)
    else:
        warnings.append("Review-only mode: JobServe Apply button was intentionally not clicked.")
        if debug_mode:
            warnings.append("Debug mode: submit is disabled and browser state was inventoried.")
        if keep_open_for_review:
            warnings.append("Browser left open for manual review; close it after reviewing the application.")

    return AssistApplyResult(
        status=status,
        filled_fields=_dedupe(filled),
        unfilled_fields=_dedupe(unfilled),
        unfilled_required_fields=_dedupe(unfilled_required),
        uploaded_cv=uploaded_cv,
        submitted=submitted,
        warnings=_dedupe(warnings),
        screenshot_path=debug.screenshot_paths[-1] if debug.screenshot_paths else None,
        profile_diagnostics=profile_diagnostics,
        timing_diagnostics={"total_runtime_ms": int((time.perf_counter() - flow_started) * 1000)},
        progress={"current_step": "submitted" if submitted else "review_required", "elapsed_ms": int((time.perf_counter() - flow_started) * 1000), "last_heartbeat_at": utcnow().isoformat()},
        upload_diagnostics=upload_diagnostics,
        select_diagnostics=select_diagnostics,
        exceptions=exceptions,
        **debug.result_kwargs(target_page),
    )


def _fill_by_label_patterns(page, patterns: list[str], candidate: FieldCandidate | None) -> str | None:
    if candidate is None:
        return None
    for pattern in patterns:
        locator = page.get_by_label(re.compile(pattern, re.I)).first
        try:
            locator.fill(candidate.value, timeout=1500)
            return pattern
        except Exception:  # noqa: BLE001
            continue
    return None


def _select_work_status(page, value: str, select_diagnostics: list[dict[str, Any]] | None = None) -> bool:
    for pattern in [r"working status", r"work status", r"status in uk", r"eligible.*uk"]:
        locator = page.get_by_label(re.compile(pattern, re.I)).first
        try:
            if _select_locator_option(locator, "UK", field_name="Working status in UK", label_pattern=pattern, diagnostics=select_diagnostics):
                return True
            locator.select_option(label=re.compile("Citizen|Permanent|Eligible|No sponsorship|UK", re.I), timeout=1500)
            return True
        except Exception:  # noqa: BLE001
            try:
                locator.fill(value, timeout=1500)
                return True
            except Exception:  # noqa: BLE001
                continue
    return False


def _handle_required_dropdown(
    page,
    candidates: dict[str, FieldCandidate],
    key: str,
    label_patterns: list[str],
    matcher,
    field_name: str,
    missing_warning: str,
    filled: list[str],
    unfilled_required: list[str],
    warnings: list[str],
    select_diagnostics: list[dict[str, Any]] | None = None,
    profile_diagnostics: dict[str, Any] | None = None,
    exceptions: list[dict[str, Any]] | None = None,
    *,
    no_match_warning: str | None = None,
) -> None:
    mapped_fields = profile_diagnostics.setdefault("mapped_fields", {}) if profile_diagnostics is not None else {}
    candidate = candidates.get(key)
    if candidate is None:
        warnings.append(missing_warning)
        unfilled_required.append(field_name)
        mapped_fields[key] = {"mapped": False, "reason": "candidate missing"}
        return
    option = matcher(candidate.value)
    if option is None:
        warnings.append(no_match_warning or f"Could not match {field_name.lower()}")
        unfilled_required.append(field_name)
        mapped_fields[key] = {"mapped": False, "reason": "profile value could not be converted to JobServe option", "value": candidate.value}
        return
    if _select_dropdown_by_label_patterns(page, label_patterns, option, field_name=field_name, diagnostics=select_diagnostics):
        filled.append(field_name)
        mapped_fields[key] = {"mapped": True, "label": field_name, "target_option": option}
        return
    warnings.append(f"Could not fill {field_name.lower()}")
    unfilled_required.append(field_name)
    select_failure = select_diagnostics[-1] if select_diagnostics else None
    mapped_fields[key] = {"mapped": False, "reason": "select option failed", "target_option": option, "select_diagnostic": select_failure}
    if exceptions is not None:
        exceptions.append(
            {
                "stage": "select",
                "type": "SelectOptionError",
                "message": f"Could not fill {field_name.lower()}",
                "traceback": None,
                "field": field_name,
                "target_option": option,
                "select_diagnostic": select_failure,
            }
        )


def _select_dropdown_by_label_patterns(
    page,
    label_patterns: list[str],
    visible_text: str,
    *,
    field_name: str | None = None,
    diagnostics: list[dict[str, Any]] | None = None,
) -> bool:
    for pattern in label_patterns:
        locator = page.get_by_label(re.compile(pattern, re.I)).first
        if _select_locator_option(locator, visible_text, field_name=field_name or visible_text, label_pattern=pattern, diagnostics=diagnostics):
            return True
    return False


def _select_locator_option(locator, visible_text: str, *, field_name: str, label_pattern: str, diagnostics: list[dict[str, Any]] | None = None) -> bool:
    diagnostic: dict[str, Any] = {
        "field": field_name,
        "label_pattern": label_pattern,
        "target": visible_text,
        "available_options": [],
        "selected_option": None,
        "strategy": None,
        "success": False,
        "failure_reason": None,
    }
    try:
        options = locator.evaluate(
            """element => Array.from(element.options || []).map((option, index) => ({
                index,
                label: option.label || option.textContent || '',
                text: option.textContent || '',
                value: option.value || ''
            }))""",
            timeout=1000,
        )
        diagnostic["available_options"] = options
    except Exception as exc:  # noqa: BLE001
        diagnostic["failure_reason"] = f"Could not inspect select options: {exc}"
        if diagnostics is not None:
            diagnostics.append(diagnostic)
        return False

    normalized_target = _normalize_select_text(visible_text)
    options = diagnostic["available_options"]
    fallback_index = _fallback_option_by_index(options, visible_text)
    attempts: list[tuple[str, Any]] = [
        ("exact_label", next((option for option in options if str(option.get("label") or option.get("text") or "") == visible_text), None)),
        ("normalized_label", next((option for option in options if _normalize_select_text(str(option.get("label") or option.get("text") or "")) == normalized_target), None)),
        ("fallback_option_index", fallback_index),
        ("partial_text", next((option for option in options if normalized_target and normalized_target in _normalize_select_text(str(option.get("label") or option.get("text") or ""))), None)),
    ]

    for strategy, option in attempts:
        if not option:
            continue
        selected, failure_reason = _select_option_candidate(locator, option, visible_text)
        if selected:
            diagnostic["strategy"] = strategy
            diagnostic["selected_option"] = option
            diagnostic["success"] = True
            if diagnostics is not None:
                diagnostics.append(diagnostic)
            return True
        diagnostic["failure_reason"] = f"{strategy} failed: {failure_reason}"

    try:
        locator.select_option(label=re.compile(re.escape(visible_text), re.I), timeout=1500)
        diagnostic["strategy"] = "regex_label"
        diagnostic["selected_option"] = visible_text
        diagnostic["success"] = True
        if diagnostics is not None:
            diagnostics.append(diagnostic)
        return True
    except Exception as exc:  # noqa: BLE001
        diagnostic["failure_reason"] = diagnostic["failure_reason"] or str(exc)
        if diagnostics is not None:
            diagnostics.append(diagnostic)
        return False


def _select_option_candidate(locator, option: dict[str, Any], visible_text: str) -> tuple[bool, str | None]:
    failures: list[str] = []
    value = str(option.get("value") or "")
    label = str(option.get("label") or option.get("text") or "")
    if value:
        try:
            locator.select_option(value=value, timeout=1500)
            return True, None
        except Exception as exc:  # noqa: BLE001
            failures.append(f"value={value!r}: {exc}")
    for candidate_label in [label, visible_text]:
        if candidate_label:
            try:
                locator.select_option(label=candidate_label, timeout=1500)
                return True, None
            except Exception as exc:  # noqa: BLE001
                failures.append(f"label={candidate_label!r}: {exc}")
    try:
        locator.select_option(index=int(option["index"]), timeout=1500)
        return True, None
    except Exception as exc:  # noqa: BLE001
        failures.append(f"index={option.get('index')!r}: {exc}")
    return False, "; ".join(failures)


def _fallback_option_by_index(options: list[dict[str, Any]], visible_text: str) -> dict[str, Any] | None:
    match = re.fullmatch(r"\s*(\d+)\s*", str(visible_text))
    if match:
        requested = int(match.group(1))
        for index in [requested, requested - 1]:
            option = next((candidate for candidate in options if candidate.get("index") == index), None)
            if option is not None:
                return option
    return None


def _normalize_select_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def salary_range_label(value: str) -> str | None:
    amount = _int_value(value)
    if amount is None:
        return None
    ranges = [
        (0, 15000, "0 - £15,000"),
        (15000, 20000, "£15,000 - £20,000"),
        (20000, 25000, "£20,000 - £25,000"),
        (25000, 30000, "£25,000 - £30,000"),
        (30000, 40000, "£30,000 - £40,000"),
        (40000, 50000, "£40,000 - £50,000"),
        (50000, 75000, "£50,000 - £75,000"),
        (75000, 100000, "£75,000 - £100,000"),
    ]
    for lower, upper, label in ranges:
        if lower <= amount <= upper:
            return label
    if amount > 100000:
        return "Above £100,000"
    return None


def travel_distance_label(value: str) -> str | None:
    miles = _int_value(value)
    if miles is None:
        return None
    if miles <= 5:
        return "0 to 5"
    if miles <= 15:
        return "6 to 15"
    if miles <= 30:
        return "16 to 30"
    if miles <= 50:
        return "31 to 50"
    return "50+"


def _int_value(value: str) -> int | None:
    match = re.search(r"\d[\d,]*", str(value))
    if not match:
        return None
    return int(match.group(0).replace(",", ""))


def _disable_jobserve_account_options(page, warnings: list[str]) -> list[str]:
    disabled: list[str] = []
    for text in ["register a Job Seeker account", "make my CV searchable", "job alerts", "create an account"]:
        controls = page.get_by_label(re.compile(text, re.I)).all()
        for control in controls:
            try:
                if control.is_checked():
                    control.uncheck()
                    warnings.append(f"Disabled option: {text}.")
                    disabled.append(text)
            except Exception:  # noqa: BLE001
                continue
    return disabled


def _jobserve_apply_button(page):
    buttons = page.get_by_role("button", name=re.compile(r"^apply$", re.I)).all()
    if buttons:
        return buttons[-1]
    return page.locator("input[type=submit], button[type=submit]").last


def _close_modal(page) -> bool:
    for locator in [page.get_by_role("button", name=re.compile(r"close", re.I)).first, page.locator(".modal button.close").first]:
        try:
            locator.click(timeout=1000)
            return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _captcha_visible(page) -> bool:
    text = page.locator("body").inner_text(timeout=3000).lower()
    return "captcha" in text or "recaptcha" in text or "hcaptcha" in text


def _submit_visible(page) -> bool:
    controls = page.locator("button, input[type=submit]").all()
    for control in controls:
        try:
            text = str(control.inner_text(timeout=500) or "")
        except Exception:  # noqa: BLE001
            text = ""
        label = " ".join([text, str(control.get_attribute("value") or "")])
        if SUBMIT_PATTERN.search(label):
            return True
    return False


def _field_label(field) -> str:
    return str(
        field.evaluate(
            """element => {
                const id = element.getAttribute('id');
                if (id) {
                    const label = document.querySelector(`label[for="${CSS.escape(id)}"]`);
                    if (label) return label.innerText || '';
                }
                const parentLabel = element.closest('label');
                if (parentLabel) return parentLabel.innerText || '';
                const described = element.getAttribute('aria-label') || element.getAttribute('placeholder') || element.getAttribute('name') || '';
                return described;
            }"""
        )
        or ""
    ).strip()


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = value.strip()
        if normalized and normalized.lower() not in seen:
            seen.add(normalized.lower())
            result.append(normalized)
    return result
