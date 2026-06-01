from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
import logging
import mimetypes
import os
from pathlib import Path
import re
import signal
import time
import traceback
from typing import Any, Callable
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError, TimeoutError as SQLAlchemyTimeoutError

from app.config import settings
from app.db.models import Job, JobScore, User
from app.db.session import SessionLocal
from app.schemas.database import AssistApplyResult
from app.services.browser_automation import chromium_diagnostics, chromium_executable_path, chromium_launch_options, launch_chromium, validate_browser_automation_availability
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
DB_LOOKUP_TIMEOUT_SECONDS = 10.0
DB_SESSION_CREATE_TIMEOUT_SECONDS = 10.0
_ACTIVE_ASSIST_JOB: dict[str, Any] | None = None


class WorkerShutdownError(RuntimeError):
    pass


class BrowserAutomationError(RuntimeError):
    def __init__(self, error: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.error = error
        self.message = message
        self.details = details or {}


def _event_loop_running() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


@dataclass(frozen=True)
class FieldCandidate:
    key: str
    value: str
    reason: str


def assist_apply_application(db: Session, job: Job, user: User, *, mode: str = "review_only", debug_mode: bool = False, browser_runner=None) -> AssistApplyResult:
    if mode not in ASSIST_MODES:
        raise ValueError("Invalid assisted apply mode.")
    progress_started = time.perf_counter()

    def progress_callback(step: str, payload: dict[str, Any]) -> None:
        _persist_assist_progress(db, job, step, payload, progress_started)

    profile = None
    try:
        progress_callback("worker_started", _assist_job_payload(job, user, mode, debug_mode))
        progress_callback("worker_handoff_entered", _assist_lookup_payload(job, user, profile, mode, debug_mode))
        progress_callback("db_session_create_start", {**_assist_lookup_payload(job, user, profile, mode, debug_mode), "db_session_already_created": True})
        progress_callback("db_session_create_done", {**_assist_lookup_payload(job, user, profile, mode, debug_mode), "db_session_already_created": True})
        progress_callback("assist_apply_application_entered", _assist_lookup_payload(job, user, profile, mode, debug_mode))
        _progress_with_db_timing(db, job, "loading_application", _assist_lookup_payload(job, user, profile, mode, debug_mode), progress_started)
        progress_callback("application_loaded", _assist_lookup_payload(job, user, profile, mode, debug_mode))
        progress_callback("loading_job", _assist_lookup_payload(job, user, profile, mode, debug_mode))
        progress_callback("job_loaded", _assist_lookup_payload(job, user, profile, mode, debug_mode))
        _timed_db_operation("loading_job", lambda: _validate_application(job), db=db, application_id=job.id)
        progress_callback("availability_check_start", _assist_lookup_payload(job, user, profile, mode, debug_mode))
        availability = _timed_availability_check(db, job)
        progress_callback(
            "availability_check_done",
            {
                **_assist_lookup_payload(job, user, profile, mode, debug_mode),
                "availability_status": availability.availability_status,
                "availability_reason": availability.availability_reason,
            },
        )
        if availability.availability_status != "active":
            raise ValueError(f"Application assistance blocked because job is {availability.availability_status}. {availability.availability_reason or ''}".strip())
        _validate_application(job)
    except Exception as exc:
        _store_assist_failure(db, job, str(exc), exc=exc, running_step=_current_assist_step(job), extra=_assist_failure_context(job, mode, debug_mode, user=user, profile=profile))
        raise

    started_at = utcnow()
    job.assisted_started_at = started_at
    job.last_apply_attempt_at = started_at
    db.commit()

    try:
        progress_callback("loading_profile", _assist_lookup_payload(job, user, profile, mode, debug_mode))
        profile = _timed_db_operation("loading_profile", lambda: get_profile(db, user), db=db, application_id=job.id)
        progress_callback("profile_loaded", _assist_lookup_payload(job, user, profile, mode, debug_mode))
        _timed_db_operation("loading_cv", lambda: _profile_cv_present(profile), db=db, application_id=job.id)
        progress_callback("resolving_job_url", _assist_lookup_payload(job, user, profile, mode, debug_mode))
        resolved_url = _resolve_assist_apply_url(job)
        progress_callback("job_url_resolved", {**_assist_lookup_payload(job, user, profile, mode, debug_mode), "resolved_job_url": resolved_url})
        if mode == "submit_with_confirmation":
            progress_callback("profile_validation_start", _assist_lookup_payload(job, user, profile, mode, debug_mode))
            _validate_jobserve_submit(db, job, user, profile)
            progress_callback("profile_validation_done", _assist_lookup_payload(job, user, profile, mode, debug_mode))
        candidates = profile_field_candidates(user, profile)
        warnings = _safety_warnings(job)
        if browser_runner:
            result = browser_runner(resolved_url, candidates, profile, mode, job.apply_strategy)
        elif debug_mode:
            result = run_playwright_assist(
                resolved_url,
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
                resolved_url,
                candidates,
                profile=profile,
                mode=mode,
                apply_strategy=job.apply_strategy,
                profile_diagnostics=profile_debug_payload(user, profile, candidates),
                job_context=jobserve_job_context(job),
                progress_callback=progress_callback,
            )
    except BrowserAutomationError as exc:
        _store_assist_failure(db, job, exc.message, error=exc.error, exc=exc, running_step=_current_assist_step(job), extra=_assist_failure_context(job, mode, debug_mode, user=user, profile=profile))
        raise
    except RuntimeError as exc:
        _store_assist_failure(db, job, str(exc), exc=exc, running_step=_current_assist_step(job), extra=_assist_failure_context(job, mode, debug_mode, user=user, profile=profile))
        raise ValueError(str(exc)) from exc
    except Exception as exc:
        _store_assist_failure(db, job, str(exc), exc=exc, running_step=_current_assist_step(job), extra=_assist_failure_context(job, mode, debug_mode, user=user, profile=profile))
        raise

    existing_result = job.assisted_result or {}
    result.warnings[:] = [*warnings, *result.warnings]
    if not result.debug_steps and isinstance(existing_result, dict):
        result.debug_steps = list(existing_result.get("debug_steps") or [])
    if not result.progress and isinstance(existing_result, dict):
        result.progress = existing_result.get("progress")
    if not result.jobserve_flow_diagnostics and isinstance(existing_result, dict):
        result.jobserve_flow_diagnostics = existing_result.get("jobserve_flow_diagnostics")
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
        result.applied_at = job.applied_at
    else:
        job.application_status = "opened"
    db.commit()
    return result


def run_assist_apply_background(application_id: int, user_id: int, mode: str = "review_only", debug_mode: bool = False) -> None:
    rq_job_id = _current_rq_job_id()
    shutdown_handler = _install_apply_shutdown_handler(application_id, user_id, mode, debug_mode, rq_job_id)
    logger.info(
        "assist_apply_worker_start service_type=%s application_id=%s user_id=%s mode=%s debug_mode=%s rq_job_id=%s database_host=%s",
        settings.service_type,
        application_id,
        user_id,
        mode,
        debug_mode,
        rq_job_id,
        _database_url_host(),
    )
    try:
        logger.info("assist_apply_worker_db_session_create_start application_id=%s user_id=%s rq_job_id=%s database_host=%s", application_id, user_id, rq_job_id, _database_url_host())
        db = _create_db_session_with_timeout(application_id, user_id, mode, debug_mode, rq_job_id)
        logger.info("assist_apply_worker_db_session_create_done application_id=%s user_id=%s rq_job_id=%s database_host=%s", application_id, user_id, rq_job_id, _database_url_host())
        try:
            job = None
            user = None
            try:
                logger.info("assist_apply_worker_loading_application application_id=%s user_id=%s rq_job_id=%s database_host=%s", application_id, user_id, rq_job_id, _database_url_host())
                job = _timed_db_operation("loading_application", lambda: db.get(Job, application_id), db=db, application_id=application_id)
                logger.info("assist_apply_worker_application_loaded application_id=%s found=%s rq_job_id=%s", application_id, job is not None, rq_job_id)
                if job is None:
                    logger.error("assist_apply_worker_missing_application error=application_not_found job_error=job_not_found service_type=%s application_id=%s user_id=%s rq_job_id=%s database_host=%s", settings.service_type, application_id, user_id, rq_job_id, _database_url_host())
                    return
                logger.info("assist_apply_worker_loading_user application_id=%s user_id=%s rq_job_id=%s", application_id, user_id, rq_job_id)
                user = _timed_db_operation("loading_job", lambda: db.get(User, user_id), db=db, application_id=application_id)
            except Exception as exc:  # noqa: BLE001
                if job is not None:
                    error = _db_lookup_error_code(exc, "application_load")
                    _store_assist_failure(db, job, error, error=error, exc=exc, running_step="loading_application", extra=_assist_worker_failure_context(job, user, None, application_id, user_id, mode, debug_mode, rq_job_id))
                logger.exception("assist_apply_worker_db_lookup_failed service_type=%s application_id=%s user_id=%s rq_job_id=%s", settings.service_type, application_id, user_id, rq_job_id)
                return
            if user is None:
                _store_assist_failure(db, job, "user_not_found", error="user_not_found", running_step="loading_application", extra=_assist_worker_failure_context(job, user, None, application_id, user_id, mode, debug_mode, rq_job_id))
                logger.error("assist_apply_worker_missing_user service_type=%s application_id=%s user_id=%s rq_job_id=%s", settings.service_type, application_id, user_id, rq_job_id)
                return
            url_resolution = _resolve_assist_apply_url_diagnostics(job)
            logger.info(
                "assist_apply_worker_context service_type=%s application_id=%s user_id=%s title=%s company=%s canonical_url=%s apply_url=%s source_job_id=%s original_external_id=%s apply_strategy=%s mode=%s debug_mode=%s rq_job_id=%s database_host=%s",
                settings.service_type,
                application_id,
                user_id,
                job.title,
                job.company_name,
                job.canonical_url,
                url_resolution["raw_apply_url"],
                job.source_job_id,
                job.original_external_id,
                job.apply_strategy,
                mode,
                debug_mode,
                rq_job_id,
                _database_url_host(),
            )
            try:
                assist_apply_application(db, job, user, mode=mode, debug_mode=debug_mode)
            except WorkerShutdownError as exc:
                if not _assist_already_failed(job):
                    _store_assist_failure(db, job, "worker_shutdown_during_apply", error="worker_shutdown_during_apply", exc=exc, running_step=_current_assist_step(job), extra=_assist_failure_context(job, mode, debug_mode, user=user, profile=None, rq_job_id=rq_job_id))
                logger.exception("assist_apply_worker_shutdown service_type=%s application_id=%s error=%s", settings.service_type, application_id, exc)
            except BrowserAutomationError as exc:
                if not _assist_already_failed(job):
                    _store_assist_failure(db, job, exc.message, error=exc.error, exc=exc, running_step=_current_assist_step(job), extra=_assist_failure_context(job, mode, debug_mode, user=user, profile=None, rq_job_id=rq_job_id))
                logger.exception(
                    "assist_apply_worker_browser_error service_type=%s application_id=%s error_code=%s error=%s",
                    settings.service_type,
                    application_id,
                    exc.error,
                    exc.message,
                )
            except Exception as exc:  # noqa: BLE001
                if not _assist_already_failed(job):
                    _store_assist_failure(db, job, str(exc), exc=exc, running_step=_current_assist_step(job), extra=_assist_failure_context(job, mode, debug_mode, user=user, profile=None, rq_job_id=rq_job_id))
                logger.exception("assist_apply_worker_failed service_type=%s application_id=%s error=%s", settings.service_type, application_id, exc)
            else:
                logger.info("assist_apply_worker_completed service_type=%s application_id=%s", settings.service_type, application_id)
        finally:
            db.close()
    finally:
        _restore_apply_shutdown_handler(shutdown_handler)


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


def _store_assist_failure(
    db: Session,
    job: Job,
    message: str,
    *,
    error: str | None = None,
    exc: BaseException | None = None,
    running_step: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    existing = job.assisted_result if isinstance(job.assisted_result, dict) else {}
    exception_payload = _exception_payload(running_step or "assist_apply", exc) if exc is not None else None
    existing_diagnostics = existing.get("jobserve_flow_diagnostics") if isinstance(existing.get("jobserve_flow_diagnostics"), dict) else {}
    diagnostics = {
        **existing_diagnostics,
        **(extra or {}),
        "browser_diagnostics": _browser_startup_diagnostics(),
        "running_step": running_step,
    }
    result = AssistApplyResult(
        status="failed",
        filled_fields=[],
        unfilled_fields=[],
        unfilled_required_fields=[],
        uploaded_cv=False,
        submitted=False,
        warnings=[f"{error}: {message}" if error else message],
        screenshot_path=None,
        final_error=message,
        progress={"current_step": running_step or "failed", "last_heartbeat_at": utcnow().isoformat(), "message": message},
        running_step=running_step or "failed",
        timing_diagnostics={"total_runtime_ms": 0},
        jobserve_flow_diagnostics=diagnostics,
        exceptions=[*(existing.get("exceptions") or []), *([exception_payload] if exception_payload else [])],
        debug_steps=list(existing.get("debug_steps") or []),
        screenshot_paths=list(existing.get("screenshot_paths") or []),
        screenshot_urls=list(existing.get("screenshot_urls") or []),
        html_snapshot_paths=list(existing.get("html_snapshot_paths") or []),
        html_snapshot_urls=list(existing.get("html_snapshot_urls") or []),
        detected_buttons=list(existing.get("detected_buttons") or []),
        detected_fields=list(existing.get("detected_fields") or []),
        detected_selects=list(existing.get("detected_selects") or []),
        detected_iframes=list(existing.get("detected_iframes") or []),
        final_url=existing.get("final_url"),
        debug_mode=bool(existing.get("debug_mode", False)),
        upload_diagnostics=existing.get("upload_diagnostics") if isinstance(existing.get("upload_diagnostics"), dict) else {},
        select_diagnostics=existing.get("select_diagnostics") if isinstance(existing.get("select_diagnostics"), list) else [],
    )
    job.assisted_result = result.model_dump()
    job.assisted_warnings = result.warnings
    job.last_apply_attempt_at = utcnow()
    db.commit()


def _create_db_session_with_timeout(application_id: int, user_id: int, mode: str, debug_mode: bool, rq_job_id: str | None) -> Session:
    started = time.perf_counter()
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(SessionLocal)
    try:
        session = future.result(timeout=DB_SESSION_CREATE_TIMEOUT_SECONDS)
    except FutureTimeoutError as exc:
        logger.exception(
            "assist_apply_worker_db_session_create_timeout application_id=%s user_id=%s rq_job_id=%s database_host=%s timeout_seconds=%s",
            application_id,
            user_id,
            rq_job_id,
            _database_url_host(),
            DB_SESSION_CREATE_TIMEOUT_SECONDS,
        )
        _persist_worker_startup_failure(
            application_id,
            user_id,
            mode,
            debug_mode,
            rq_job_id,
            "db_session_create_timeout",
            exc,
            "db_session_create_start",
        )
        executor.shutdown(wait=False, cancel_futures=True)
        raise TimeoutError("db_session_create_timeout") from exc
    finally:
        if future.done():
            executor.shutdown(wait=False)
    duration_ms = int((time.perf_counter() - started) * 1000)
    if duration_ms > DB_SESSION_CREATE_TIMEOUT_SECONDS * 1000:
        exc = TimeoutError("db_session_create_timeout")
        _persist_worker_startup_failure(application_id, user_id, mode, debug_mode, rq_job_id, "db_session_create_timeout", exc, "db_session_create_start")
        raise exc
    return session


def _persist_worker_startup_failure(
    application_id: int,
    user_id: int,
    mode: str,
    debug_mode: bool,
    rq_job_id: str | None,
    error: str,
    exc: BaseException,
    running_step: str,
) -> None:
    try:
        with SessionLocal() as db:
            job = db.get(Job, application_id)
            if job is None or _assist_already_failed(job):
                return
            _store_assist_failure(
                db,
                job,
                error,
                error=error,
                exc=exc,
                running_step=running_step,
                extra=_assist_worker_failure_context(job, None, None, application_id, user_id, mode, debug_mode, rq_job_id),
            )
    except Exception as persist_exc:  # noqa: BLE001
        logger.exception("assist_apply_worker_startup_failure_persist_failed application_id=%s error=%s", application_id, persist_exc)


def _install_apply_shutdown_handler(application_id: int, user_id: int, mode: str, debug_mode: bool, rq_job_id: str | None):
    global _ACTIVE_ASSIST_JOB
    _ACTIVE_ASSIST_JOB = {
        "application_id": application_id,
        "user_id": user_id,
        "mode": mode,
        "debug_mode": debug_mode,
        "rq_job_id": rq_job_id,
    }
    try:
        previous = signal.getsignal(signal.SIGTERM)
        _ACTIVE_ASSIST_JOB["previous_sigterm_handler"] = previous
        signal.signal(signal.SIGTERM, _assist_apply_sigterm_handler)
        return previous
    except Exception as exc:  # noqa: BLE001
        logger.warning("assist_apply_shutdown_handler_install_failed application_id=%s error=%s", application_id, exc)
        return None


def _restore_apply_shutdown_handler(previous_handler) -> None:
    global _ACTIVE_ASSIST_JOB
    _ACTIVE_ASSIST_JOB = None
    if previous_handler is None:
        return
    try:
        signal.signal(signal.SIGTERM, previous_handler)
    except Exception as exc:  # noqa: BLE001
        logger.warning("assist_apply_shutdown_handler_restore_failed error=%s", exc)


def _assist_apply_sigterm_handler(signum, frame) -> None:
    context = dict(_ACTIVE_ASSIST_JOB or {})
    application_id = context.get("application_id")
    if application_id is not None:
        _persist_worker_shutdown_during_apply(int(application_id), context)
    previous = context.get("previous_sigterm_handler")
    if callable(previous) and previous is not _assist_apply_sigterm_handler:
        previous(signum, frame)
    raise WorkerShutdownError("worker_shutdown_during_apply")


def _persist_worker_shutdown_during_apply(application_id: int, context: dict[str, Any]) -> None:
    try:
        with SessionLocal() as db:
            job = db.get(Job, application_id)
            if job is None or _assist_already_failed(job):
                return
            _store_assist_failure(
                db,
                job,
                "worker_shutdown_during_apply",
                error="worker_shutdown_during_apply",
                exc=WorkerShutdownError("worker_shutdown_during_apply"),
                running_step=_current_assist_step(job),
                extra=_assist_worker_failure_context(
                    job,
                    None,
                    None,
                    application_id,
                    int(context.get("user_id") or 0),
                    str(context.get("mode") or "review_only"),
                    bool(context.get("debug_mode")),
                    context.get("rq_job_id"),
                ),
            )
    except Exception as exc:  # noqa: BLE001
        logger.exception("assist_apply_worker_shutdown_persist_failed application_id=%s error=%s", application_id, exc)


def _progress_with_db_timing(db: Session, job: Job, step: str, payload: dict[str, Any], started_perf: float) -> None:
    started = time.perf_counter()
    logger.info("%s_start application_id=%s database_host=%s pool_status=%s", step, job.id, _database_url_host(), _db_pool_status(db))
    try:
        _persist_assist_progress(db, job, step, {**payload, "db_diagnostics": _db_diagnostics(db, job.id, None, True)}, started_perf)
    except SQLAlchemyTimeoutError as exc:
        raise TimeoutError(_progress_timeout_code(step)) from exc
    except SQLAlchemyError as exc:
        raise RuntimeError("database_lookup_error") from exc
    duration_ms = int((time.perf_counter() - started) * 1000)
    logger.info("%s_done application_id=%s duration_ms=%s database_host=%s pool_status=%s", step, job.id, duration_ms, _database_url_host(), _db_pool_status(db))
    if duration_ms > DB_LOOKUP_TIMEOUT_SECONDS * 1000:
        raise TimeoutError(_progress_timeout_code(step))


def _timed_db_operation(name: str, operation: Callable[[], Any], *, db: Session | None = None, application_id: int | None = None) -> Any:
    started = time.perf_counter()
    connection_acquired = False
    pool_status = _db_pool_status(db)
    logger.info("%s_start application_id=%s database_host=%s pool_status=%s", name, application_id, _database_url_host(), pool_status)
    try:
        if db is not None:
            connection_acquired = db.connection() is not None
        result = operation()
    except SQLAlchemyTimeoutError as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.exception(
            "%s_timeout application_id=%s query_duration_ms=%s database_host=%s pool_status=%s connection_acquired=%s",
            name,
            application_id,
            duration_ms,
            _database_url_host(),
            _db_pool_status(db),
            connection_acquired,
        )
        raise TimeoutError(_db_operation_timeout_code(name)) from exc
    except SQLAlchemyError as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.exception(
            "%s_database_error application_id=%s query_duration_ms=%s database_host=%s pool_status=%s connection_acquired=%s",
            name,
            application_id,
            duration_ms,
            _database_url_host(),
            _db_pool_status(db),
            connection_acquired,
        )
        raise RuntimeError("database_lookup_error") from exc
    duration_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "%s_done application_id=%s duration_ms=%s database_host=%s pool_status=%s connection_acquired=%s",
        name,
        application_id,
        duration_ms,
        _database_url_host(),
        _db_pool_status(db),
        connection_acquired,
    )
    if duration_ms > DB_LOOKUP_TIMEOUT_SECONDS * 1000:
        raise TimeoutError(_db_operation_timeout_code(name))
    return result


def _timed_availability_check(db: Session, job: Job):
    started = time.perf_counter()
    logger.info("availability_check_start application_id=%s database_host=%s pool_status=%s", job.id, _database_url_host(), _db_pool_status(db))
    try:
        result = check_job_availability(db, job)
    except SQLAlchemyError as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.exception(
            "availability_check_database_error application_id=%s duration_ms=%s database_host=%s pool_status=%s",
            job.id,
            duration_ms,
            _database_url_host(),
            _db_pool_status(db),
        )
        raise RuntimeError("database_lookup_error") from exc
    except Exception:
        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.exception(
            "availability_check_failed application_id=%s duration_ms=%s database_host=%s pool_status=%s",
            job.id,
            duration_ms,
            _database_url_host(),
            _db_pool_status(db),
        )
        raise
    duration_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "availability_check_done application_id=%s duration_ms=%s availability_status=%s availability_reason=%s database_host=%s pool_status=%s",
        job.id,
        duration_ms,
        result.availability_status,
        result.availability_reason,
        _database_url_host(),
        _db_pool_status(db),
    )
    return result


def _db_operation_timeout_code(name: str) -> str:
    if name == "loading_application":
        return "application_load_timeout"
    if name == "loading_job":
        return "job_load_timeout"
    if name == "loading_profile":
        return "profile_load_timeout"
    if name == "loading_cv":
        return "cv_load_timeout"
    return f"{name}_timeout"


def _progress_timeout_code(step: str) -> str:
    if step == "loading_application":
        return "application_load_timeout"
    if step == "loading_profile":
        return "profile_load_timeout"
    return f"{step}_timeout"


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
        "status": "running" if existing.get("status") in {None, "queued"} else existing.get("status"),
        "filled_fields": existing.get("filled_fields", []),
        "unfilled_fields": existing.get("unfilled_fields", []),
        "unfilled_required_fields": existing.get("unfilled_required_fields", []),
        "uploaded_cv": existing.get("uploaded_cv", False),
        "submitted": existing.get("submitted", False),
        "warnings": existing.get("warnings", []),
        "screenshot_path": existing.get("screenshot_path"),
        "running_step": step,
        "progress": progress,
        "timing_diagnostics": {**existing.get("timing_diagnostics", {}), "total_runtime_ms": progress["elapsed_ms"]},
        "debug_steps": [*existing.get("debug_steps", []), {"step": step, **payload}][-100:],
    }
    job.last_apply_attempt_at = utcnow()
    db.commit()


def mark_queued_assist(db: Session, job: Job, *, mode: str, debug_mode: bool) -> None:
    result = queued_assist_apply_result().model_dump()
    result["debug_steps"] = [{"step": "queued", **_assist_job_payload(job, None, mode, debug_mode)}]
    result["jobserve_flow_diagnostics"] = _assist_failure_context(job, mode, debug_mode)
    job.assisted_result = result
    job.last_apply_attempt_at = utcnow()
    db.commit()


def update_queued_assist_metadata(db: Session, job: Job, *, rq_job_id: str, queue_name: str, redis_host: str) -> AssistApplyResult:
    result = job.assisted_result or queued_assist_apply_result().model_dump()
    progress = result.get("progress") if isinstance(result.get("progress"), dict) else {}
    diagnostics = result.get("jobserve_flow_diagnostics") if isinstance(result.get("jobserve_flow_diagnostics"), dict) else {}
    result = {
        **result,
        "status": "queued",
        "progress": {
            **progress,
            "current_step": "queued",
            "message": "queued",
            "last_heartbeat_at": utcnow().isoformat(),
            "rq_job_id": rq_job_id,
            "queue_name": queue_name,
            "redis_host": redis_host,
        },
        "jobserve_flow_diagnostics": {
            **diagnostics,
            "rq_job_id": rq_job_id,
            "queue_name": queue_name,
            "redis_host": redis_host,
        },
        "debug_steps": [*result.get("debug_steps", []), {"step": "queued_to_rq", "rq_job_id": rq_job_id, "queue_name": queue_name, "redis_host": redis_host}][-100:],
    }
    job.assisted_result = result
    db.commit()
    return AssistApplyResult(**result)


def _progress_message(step: str) -> str:
    if "browser" in step:
        return "browser startup"
    if "availability" in step:
        return "checking job availability"
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
        "apply_url": getattr(job, "apply_url", None) or job.canonical_url,
    }


def _resolve_assist_apply_url(job: Job) -> str:
    diagnostics = _resolve_assist_apply_url_diagnostics(job)
    selected_url = str(diagnostics.get("selected_url") or "").strip()
    if selected_url:
        logger.info(
            "assist_apply_url_resolved application_id=%s selected_url=%s raw_apply_url=%s raw_canonical_url=%s raw_job_url=%s source_job_id=%s rejected_urls=%s",
            job.id,
            selected_url,
            diagnostics.get("raw_apply_url"),
            diagnostics.get("raw_canonical_url"),
            diagnostics.get("raw_job_url"),
            diagnostics.get("source_job_id"),
            diagnostics.get("rejected_urls"),
        )
        return selected_url
    if diagnostics.get("is_jobserve"):
        logger.warning(
            "assist_apply_url_resolution_failed application_id=%s reason=missing_saved_jobserve_url raw_apply_url=%s raw_canonical_url=%s raw_job_url=%s source_job_id=%s rejected_urls=%s",
            job.id,
            diagnostics.get("raw_apply_url"),
            diagnostics.get("raw_canonical_url"),
            diagnostics.get("raw_job_url"),
            diagnostics.get("source_job_id"),
            diagnostics.get("rejected_urls"),
        )
        raise ValueError("missing_saved_jobserve_url")
    if not str(job.canonical_url or "").strip():
        raise ValueError("Missing apply URL.")
    raise ValueError("Missing apply URL.")


def _resolve_assist_apply_url_diagnostics(job: Job) -> dict[str, Any]:
    raw_apply_url = str(getattr(job, "apply_url", None) or "").strip()
    raw_canonical_url = str(getattr(job, "canonical_url", None) or "").strip()
    raw_job_url = str(getattr(job, "url", None) or "").strip()
    identifiers = [str(value).strip() for value in [getattr(job, "source_job_id", None), getattr(job, "original_external_id", None)] if str(value or "").strip()]
    is_jobserve = "jobserve" in str(getattr(job, "apply_strategy", "") or "").lower() or any("jobserve.com" in url.lower() for url in [raw_apply_url, raw_canonical_url, raw_job_url])
    diagnostics: dict[str, Any] = {
        "raw_apply_url": raw_apply_url,
        "raw_canonical_url": raw_canonical_url,
        "raw_job_url": raw_job_url,
        "source_job_id": getattr(job, "source_job_id", None),
        "original_external_id": getattr(job, "original_external_id", None),
        "selected_url": None,
        "selected_url_source": None,
        "is_jobserve": is_jobserve,
        "rejected_urls": [],
    }
    seen: set[str] = set()
    candidates = [("apply_url", raw_apply_url), ("canonical_url", raw_canonical_url), ("job_url", raw_job_url)]
    for source, url in candidates:
        if not url or url in seen:
            continue
        seen.add(url)
        if not is_jobserve:
            diagnostics["selected_url"] = url
            diagnostics["selected_url_source"] = source
            return diagnostics
        valid, reason = _jobserve_specific_url_status(url, identifiers=identifiers)
        if valid:
            diagnostics["selected_url"] = url
            diagnostics["selected_url_source"] = source
            return diagnostics
        diagnostics["rejected_urls"].append({"source": source, "url": url, "reason": reason})
    if is_jobserve:
        reconstructed = _jobserve_reconstruct_url_from_id(identifiers)
        if reconstructed:
            diagnostics["selected_url"] = reconstructed
            diagnostics["selected_url_source"] = "source_job_id"
    return diagnostics


def _jobserve_should_try_direct_url(url: str | None, *, source_job_id: str | None = None, original_external_id: str | None = None) -> bool:
    valid, _reason = _jobserve_specific_url_status(url, identifiers=[source_job_id, original_external_id])
    return valid


def _jobserve_specific_url_status(url: str | None, *, identifiers: list[str | None] | None = None) -> tuple[bool, str | None]:
    if not url:
        return False, "empty_url"
    lowered = url.lower()
    if "jobserve.com" not in lowered:
        return False, "not_jobserve"
    parsed = urlparse(url)
    path = (parsed.path or "").lower()
    query = (parsed.query or "").lower()
    if "jobsearch.aspx" in path or path.rstrip("/").endswith("/job-search"):
        return False, "generic_jobserve_search_url"
    cleaned_identifiers = [str(identifier).strip().lower() for identifier in identifiers or [] if str(identifier or "").strip()]
    if any(identifier and identifier in lowered for identifier in cleaned_identifiers):
        return True, None
    if any(token in lowered for token in ["/job/", "jobid", "job=", "fasttrack", "apply"]):
        return True, None
    slug = path.rstrip("/").split("/")[-1]
    if "search-jobs-in-" in path and re.search(r"-[a-z0-9]{8,}$", slug, re.I):
        return True, None
    if re.search(r"(?:^|[?&])job(?:id)?=[a-z0-9-]{6,}", query, re.I):
        return True, None
    return False, "jobserve_url_not_specific"


def _jobserve_reconstruct_url_from_id(identifiers: list[str]) -> str | None:
    for identifier in identifiers:
        if re.fullmatch(r"[A-Za-z0-9]{8,}", identifier):
            return f"https://www.jobserve.com/gb/en/job/{identifier}"
    return None


def _assist_job_payload(job: Job, user: User | None, mode: str, debug_mode: bool) -> dict[str, Any]:
    url_resolution = _resolve_assist_apply_url_diagnostics(job)
    return {
        "application_id": job.id,
        "user_id": getattr(user, "id", None),
        "job_title": job.title,
        "job_company": job.company_name,
        "canonical_url": job.canonical_url,
        "apply_url": url_resolution["raw_apply_url"] or job.canonical_url,
        "raw_apply_url": url_resolution["raw_apply_url"],
        "raw_canonical_url": url_resolution["raw_canonical_url"],
        "raw_job_url": url_resolution["raw_job_url"],
        "selected_url": url_resolution["selected_url"],
        "selected_url_source": url_resolution["selected_url_source"],
        "rejected_urls": url_resolution["rejected_urls"],
        "source_job_id": job.source_job_id,
        "original_external_id": job.original_external_id,
        "apply_strategy": job.apply_strategy,
        "mode": mode,
        "debug_mode": debug_mode,
    }


def _assist_lookup_payload(job: Job, user: User | None, profile, mode: str, debug_mode: bool) -> dict[str, Any]:
    return {
        **_assist_job_payload(job, user, mode, debug_mode),
        "db_lookup": _assist_lookup_status(job, user, profile),
        "queue_job_id": _queued_rq_job_id(job) or _current_rq_job_id(),
        "database_host": _database_url_host(),
    }


def _assist_lookup_status(job: Job | None, user: User | None, profile) -> dict[str, Any]:
    return {
        "application_found": job is not None,
        "job_found": job is not None,
        "profile_found": profile is not None,
        "cv_found": _profile_cv_present(profile),
    }


def _profile_cv_present(profile) -> bool:
    return bool(profile and (getattr(profile, "cv_file_path", None) or getattr(profile, "cv_file_bytes", None)))


def _assist_worker_failure_context(
    job: Job | None,
    user: User | None,
    profile,
    application_id: int,
    user_id: int,
    mode: str,
    debug_mode: bool,
    rq_job_id: str | None,
) -> dict[str, Any]:
    base = _assist_failure_context(job, mode, debug_mode, user=user, profile=profile, rq_job_id=rq_job_id) if job is not None else {}
    return {
        **base,
        "application_id": application_id,
        "job_id": getattr(job, "id", None),
        "user_id": user_id,
        "queue_job_id": rq_job_id,
        "queue_name": settings.queue_name,
        "database_host": _database_url_host(),
        "db_diagnostics": _db_diagnostics(None, application_id, None, job is not None),
        "db_lookup": _assist_lookup_status(job, user, profile),
    }


def _assist_failure_context(job: Job, mode: str, debug_mode: bool, *, user: User | None = None, profile=None, rq_job_id: str | None = None) -> dict[str, Any]:
    url_resolution = _resolve_assist_apply_url_diagnostics(job)
    return {
        **_assist_job_payload(job, None, mode, debug_mode),
        "resolved_job_url": url_resolution["selected_url"] or str(job.canonical_url or "").strip(),
        "url_resolution": url_resolution,
        "application_id": job.id,
        "job_id": job.id,
        "user_id": getattr(user, "id", None),
        "queue_job_id": rq_job_id or _queued_rq_job_id(job) or _current_rq_job_id(),
        "queue_name": settings.queue_name,
        "database_host": _database_url_host(),
        "db_diagnostics": _db_diagnostics(None, job.id, None, True),
        "db_lookup": _assist_lookup_status(job, user, profile),
        "browser_diagnostics": _browser_startup_diagnostics(),
    }


def _queued_rq_job_id(job: Job | None) -> str | None:
    if job is None:
        return None
    result = job.assisted_result or {}
    progress = result.get("progress") if isinstance(result.get("progress"), dict) else {}
    diagnostics = result.get("jobserve_flow_diagnostics") if isinstance(result.get("jobserve_flow_diagnostics"), dict) else {}
    return progress.get("rq_job_id") or diagnostics.get("rq_job_id")


def _current_rq_job_id() -> str | None:
    try:
        from rq import get_current_job

        current = get_current_job()
        return str(current.id) if current is not None else None
    except Exception:  # noqa: BLE001
        return None


def _database_url_host() -> str:
    parsed = urlparse(settings.database_url)
    return parsed.hostname or "unknown"


def _db_pool_status(db: Session | None) -> str | None:
    try:
        bind = db.get_bind() if db is not None else SessionLocal.kw["bind"]
        pool = getattr(bind, "pool", None)
        status = getattr(pool, "status", None)
        return status() if callable(status) else None
    except Exception:  # noqa: BLE001
        return None


def _db_diagnostics(db: Session | None, application_id: int | None, query_duration_ms: int | None, connection_acquired: bool | None) -> dict[str, Any]:
    return {
        "database_host": _database_url_host(),
        "pool_status": _db_pool_status(db),
        "connection_acquired": connection_acquired,
        "application_id": application_id,
        "query_duration_ms": query_duration_ms,
    }


def _db_lookup_error_code(exc: BaseException, fallback: str) -> str:
    message = str(exc)
    if isinstance(exc, TimeoutError) or message.endswith("_timeout"):
        return message if message.endswith("_timeout") else f"{fallback}_timeout"
    return "database_lookup_error"


def _browser_startup_diagnostics(*, browser_launch_succeeded: bool | None = None, launch_duration_ms: int | None = None, headless: bool | None = None) -> dict[str, Any]:
    diagnostics = chromium_diagnostics()
    return {
        "playwright_enabled": settings.playwright_enabled,
        "service_type": settings.service_type,
        "app_env": settings.app_env,
        "render": bool(os.environ.get("RENDER")),
        "chromium_executable_path": diagnostics.get("chromium_executable_path"),
        "chromium_file_exists": diagnostics.get("chromium_file_exists"),
        "chromium_file_executable": diagnostics.get("chromium_file_executable"),
        "playwright_browsers_path": diagnostics.get("playwright_browsers_path"),
        "browser_launch_succeeded": browser_launch_succeeded,
        "launch_duration_ms": launch_duration_ms,
        "headless": headless,
        "timeout_ms": 30000,
    }


def _current_assist_step(job: Job) -> str | None:
    existing = job.assisted_result or {}
    progress = existing.get("progress") if isinstance(existing, dict) else {}
    if isinstance(progress, dict):
        return progress.get("current_step") or existing.get("running_step")
    return existing.get("running_step") if isinstance(existing, dict) else None


def _assist_already_failed(job: Job) -> bool:
    existing = job.assisted_result or {}
    return isinstance(existing, dict) and existing.get("status") == "failed"


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
    use_current_selected_job_as_intended: bool = False,
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
    if _event_loop_running():
        raise BrowserAutomationError("playwright_api_mismatch", "Sync Playwright API called inside asyncio loop")
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # noqa: BLE001
        raise BrowserAutomationError("playwright_not_installed", "Playwright is not installed in this environment.") from exc

    headless = settings.app_env.lower() in {"production", "prod", "render"}
    filled: list[str] = []
    unfilled: list[str] = []
    warnings: list[str] = []
    launch_options = chromium_launch_options(headless=headless)
    browser_started = time.perf_counter()
    _report_stage(progress_callback, "browser_launch", "start", launch_options={key: value for key, value in launch_options.items() if key != "executable_path"}, browser_diagnostics=_browser_startup_diagnostics(headless=headless))
    try:
        with sync_playwright() as playwright:
            try:
                browser = launch_chromium(playwright, validate=False, **launch_options)
            except Exception as exc:  # noqa: BLE001
                if progress_callback:
                    progress_callback("browser_launch_failed", {"browser_diagnostics": _browser_startup_diagnostics(browser_launch_succeeded=False, launch_duration_ms=int((time.perf_counter() - browser_started) * 1000), headless=headless), "error": str(exc)})
                raise BrowserAutomationError("browser_launch", str(exc)) from exc
            timing_diagnostics["browser_startup_ms"] = int((time.perf_counter() - browser_started) * 1000)
            _report_stage(progress_callback, "browser_launch", "done", browser_diagnostics=_browser_startup_diagnostics(browser_launch_succeeded=True, launch_duration_ms=timing_diagnostics["browser_startup_ms"], headless=headless))
            if progress_callback:
                progress_callback("browser_launch_success", {"browser_diagnostics": _browser_startup_diagnostics(browser_launch_succeeded=True, launch_duration_ms=timing_diagnostics["browser_startup_ms"], headless=headless)})
            keep_open_for_review = not headless
            try:
                if progress_callback:
                    progress_callback("context_created", {"context_count": len(getattr(browser, "contexts", []) or [])})
                page = browser.new_page()
                if progress_callback:
                    progress_callback("page_created", {"url": page.url})
                page.set_default_timeout(settings.playwright_step_timeout_ms)
                page.set_default_navigation_timeout(settings.page_navigation_timeout_ms)
                if apply_strategy == "jobserve_apply_easy":
                    if _jobserve_should_try_direct_url(url):
                        try:
                            if progress_callback:
                                progress_callback("navigating_to_job_url", {"url": url, "job_context": job_context or {"canonical_url": url}})
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
                                job_context=job_context or {"canonical_url": url},
                                direct_url=url,
                            )
                            if progress_callback:
                                progress_callback("job_page_loaded", {"final_url": result.final_url, "jobserve_flow_diagnostics": result.jobserve_flow_diagnostics})
                            result.timing_diagnostics = {**result.timing_diagnostics, **timing_diagnostics, "total_runtime_ms": int((time.perf_counter() - total_started) * 1000)}
                            return result
                        except BrowserAutomationError:
                            raise
                        except RuntimeError as exc:
                            logger.warning("jobserve_direct_apply_failed_no_search_fallback error=%s", exc)
                            return AssistApplyResult(
                                status="review_required",
                                filled_fields=[],
                                unfilled_fields=[],
                                unfilled_required_fields=[],
                                uploaded_cv=False,
                                submitted=False,
                                warnings=[str(exc)],
                                screenshot_path=None,
                                final_error=str(exc),
                                jobserve_flow_diagnostics={"mode": "direct_job_url", "direct_url": url, "target": job_context or {"canonical_url": url}, "blocked_reason": str(exc)},
                                timing_diagnostics={**timing_diagnostics, "total_runtime_ms": int((time.perf_counter() - total_started) * 1000)},
                            )

                    try:
                        if progress_callback:
                            progress_callback("jobserve_flow_started", {"mode": "search_to_apply", "url": url, "job_context": job_context or {"canonical_url": url}})
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
                            use_current_selected_job_as_intended=use_current_selected_job_as_intended,
                        )
                        result.timing_diagnostics = {**result.timing_diagnostics, **timing_diagnostics, "total_runtime_ms": int((time.perf_counter() - total_started) * 1000)}
                        return result
                    except BrowserAutomationError:
                        raise
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
                if progress_callback:
                    progress_callback("navigating_to_job_url", {"url": url})
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
                if progress_callback:
                    progress_callback("job_page_loaded", {"final_url": page.url})
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
        if "timeout" in message.lower() and "30000" in message:
            raise BrowserAutomationError("browser_startup_timeout", "Browser startup timed out after 30 seconds.") from exc
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
    if not profile:
        raise ValueError("profile_not_found")
    if not (getattr(profile, "cv_file_path", None) or getattr(profile, "cv_file_bytes", None)):
        raise ValueError("Saved CV file is required before submitting a JobServe application.")
    if not ((getattr(profile, "email", None) or "").strip() or (getattr(user, "email", None) or "").strip()):
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


def _raise_jobserve_stage(
    debug: _ApplyDebugRecorder,
    page,
    browser,
    stage: str,
    message: str,
    *,
    target=None,
    exc: BaseException | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    target_page = target or page
    debug.final_error = message
    debug.screenshot(f"{stage}_failed")
    debug.html(f"{stage}_failed", target_page)
    inventory = _inventory_browser(target_page, browser)
    details = {
        "failed_phase": stage,
        "exact_error": message,
        "current_url": _safe_url(target_page),
        "page_title": _safe_title(target_page),
        "screenshot_paths": list(debug.screenshot_paths),
        "screenshot_urls": [_artifact_url(path) for path in debug.screenshot_paths],
        "html_snapshot_paths": list(debug.html_snapshot_paths),
        "html_snapshot_urls": [_artifact_url(path) for path in debug.html_snapshot_paths],
        "detected_buttons": inventory["buttons"],
        "detected_fields": inventory["fields"],
        "detected_selects": inventory["selects"],
        "detected_iframes": inventory["iframes"],
        "debug_steps": list(debug.steps),
    }
    if exc is not None:
        details["exception"] = _exception_payload(stage, exc)
        details["traceback"] = details["exception"].get("traceback")
    if extra:
        details.update(extra)
    raise BrowserAutomationError(stage, message, details)


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
            context.locator('[id*="detail" i] button, [id*="detail" i] a, [id*="detail" i] input[type=button], [id*="detail" i] input[type=submit], [class*="detail" i] button, [class*="detail" i] a, [class*="detail" i] input[type=button], [class*="detail" i] input[type=submit]').filter(has_text=re.compile(r"^apply(\s+now)?$", re.I)).first,
            context.locator('main button, main a, main input[type=button], main input[type=submit]').filter(has_text=re.compile(r"^apply(\s+now)?$", re.I)).first,
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
    use_current_selected_job_as_intended: bool = False,
) -> AssistApplyResult:
    flow_started = time.perf_counter()
    timing_diagnostics: dict[str, Any] = {}
    flow: dict[str, Any] = {
        "mode": "search_to_apply",
        "search_url": JOBSERVE_SEARCH_URL,
        "search_defaults": _jobserve_search_preferences(profile),
        "target": job_context,
        "identity_source": job_context.get("identity_source") or "db",
        "search_page_loaded": False,
        "search_controls": {},
        "search_button_clicked": False,
        "results_loaded": False,
        "first_job_selected": False,
        "intended_job_identity": job_context,
        "auto_selected_result_identity": None,
        "auto_selected_matched": False,
        "selected_result_identity": None,
        "verified_detail_panel_identity": None,
        "modal_identity": None,
        "submitted_identity": None,
        "blocked_reason": None,
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

    if not _fill_jobserve_search_form(page, flow, select_diagnostics, step_callback=progress_callback, screenshot_callback=debug.screenshot):
        debug.final_error = "JobServe search form could not be filled."
        debug.html("search_form_failed", page)
        raise RuntimeError(debug.final_error)
    debug.step("jobserve_search_form_filled", jobserve_flow_diagnostics=flow)

    search_click_diagnostics: dict[str, Any] = {}
    if not _click_jobserve_search(page, search_click_diagnostics):
        flow["search_click_diagnostics"] = search_click_diagnostics
        debug.final_error = "JobServe search button could not be clicked."
        debug.screenshot("search_button_click_failed")
        debug.html("search_form_failed", page)
        raise RuntimeError(debug.final_error)
    flow["search_button_clicked"] = True
    flow["search_click_diagnostics"] = search_click_diagnostics
    try:
        page.wait_for_load_state("domcontentloaded", timeout=20000)
    except Exception:  # noqa: BLE001
        pass
    page.wait_for_timeout(1200)
    debug.step("jobserve_search_submitted", jobserve_flow_diagnostics=flow)
    debug.screenshot("search_results_loaded")
    flow["results_loaded"] = True
    debug.step("jobserve_results_loaded", jobserve_flow_diagnostics=flow)

    if use_current_selected_job_as_intended and not _jobserve_target_has_identity(job_context):
        job_context = _jobserve_use_current_selected_job_as_intended(page, flow)
        if _jobserve_target_has_identity(job_context):
            flow["target"] = job_context
            flow["intended_job_identity"] = job_context
            debug.step("jobserve_current_selected_job_used_as_intended", jobserve_flow_diagnostics=flow)
        else:
            timing_diagnostics["result_matching_ms"] = 0
            debug.final_error = flow.get("blocked_reason") or "Could not read current selected JobServe job identity"
            debug.screenshot("current_selected_job_identity_missing")
            debug.html("current_selected_job_identity_missing", page)
            warnings.append(debug.final_error)
            return AssistApplyResult(
                status="review_required",
                filled_fields=[],
                unfilled_fields=[],
                unfilled_required_fields=[],
                uploaded_cv=False,
                submitted=False,
                warnings=_dedupe(warnings),
                screenshot_path=debug.screenshot_paths[-1] if debug.screenshot_paths else None,
                profile_diagnostics=profile_diagnostics,
                jobserve_flow_diagnostics=flow,
                timing_diagnostics={**timing_diagnostics, "total_runtime_ms": int((time.perf_counter() - flow_started) * 1000)},
                progress={"current_step": "review_required", "elapsed_ms": int((time.perf_counter() - flow_started) * 1000), "last_heartbeat_at": utcnow().isoformat()},
                upload_diagnostics=upload_diagnostics,
                select_diagnostics=select_diagnostics,
                exceptions=exceptions,
                **debug.result_kwargs(page),
            )

    match_started = time.perf_counter()
    verified_identity = _verify_or_select_intended_jobserve_result(page, browser, job_context, flow)
    selected = verified_identity
    timing_diagnostics["result_matching_ms"] = int((time.perf_counter() - match_started) * 1000)
    if selected is None:
        debug.final_error = flow.get("blocked_reason") or "Intended JobServe job not found in results"
        debug.html("no_matching_job_found", page)
        warnings.append(debug.final_error)
        return AssistApplyResult(
            status="review_required",
            filled_fields=[],
            unfilled_fields=[],
            unfilled_required_fields=[],
            uploaded_cv=False,
            submitted=False,
            warnings=_dedupe(warnings),
            screenshot_path=debug.screenshot_paths[-1] if debug.screenshot_paths else None,
            profile_diagnostics=profile_diagnostics,
            jobserve_flow_diagnostics=flow,
            timing_diagnostics={**timing_diagnostics, "total_runtime_ms": int((time.perf_counter() - flow_started) * 1000)},
            progress={"current_step": "review_required", "elapsed_ms": int((time.perf_counter() - flow_started) * 1000), "last_heartbeat_at": utcnow().isoformat()},
            upload_diagnostics=upload_diagnostics,
            select_diagnostics=select_diagnostics,
            exceptions=exceptions,
            **debug.result_kwargs(page),
        )
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
    _retry_step("apply button click", lambda: _click_locator_resilient(page, apply_target))
    flow["apply_button_clicked"] = True
    debug.step("jobserve_apply_button_clicked", jobserve_flow_diagnostics=flow)
    debug.screenshot("after_apply_button_clicked")
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
    modal_identity = _jobserve_modal_identity(context)
    flow["modal_identity"] = modal_identity
    debug.step("jobserve_application_modal_opened", jobserve_flow_diagnostics=flow, modal_identity=modal_identity)
    debug.screenshot("application_modal_opened")
    if _jobserve_identity_clear_mismatch(modal_identity, verified_identity):
        flow["blocked_reason"] = "JobServe application modal does not match intended job"
        warnings.append(flow["blocked_reason"])
        debug.html("modal_identity_mismatch", context)
        return AssistApplyResult(
            status="review_required",
            filled_fields=[],
            unfilled_fields=[],
            unfilled_required_fields=[],
            uploaded_cv=False,
            submitted=False,
            warnings=_dedupe(warnings),
            screenshot_path=debug.screenshot_paths[-1] if debug.screenshot_paths else None,
            profile_diagnostics=profile_diagnostics,
            jobserve_flow_diagnostics=flow,
            timing_diagnostics={**timing_diagnostics, "total_runtime_ms": int((time.perf_counter() - flow_started) * 1000)},
            progress={"current_step": "review_required", "elapsed_ms": int((time.perf_counter() - flow_started) * 1000), "last_heartbeat_at": utcnow().isoformat()},
            upload_diagnostics=upload_diagnostics,
            select_diagnostics=select_diagnostics,
            exceptions=exceptions,
            **debug.result_kwargs(page),
        )

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
        submit_guard = _jobserve_submit_guard(flow, verified_identity, modal_identity, context, uploaded_cv, unfilled_required)
        if submit_guard:
            flow["blocked_reason"] = submit_guard
            warnings.append(submit_guard)
            debug.html("submit_guard_blocked", context)
            return AssistApplyResult(
                status="review_required",
                filled_fields=_dedupe(filled),
                unfilled_fields=_dedupe(unfilled),
                unfilled_required_fields=_dedupe(unfilled_required),
                uploaded_cv=uploaded_cv,
                submitted=False,
                warnings=_dedupe(warnings),
                screenshot_path=debug.screenshot_paths[-1] if debug.screenshot_paths else None,
                profile_diagnostics=profile_diagnostics,
                jobserve_flow_diagnostics=flow,
                timing_diagnostics={**timing_diagnostics, "total_runtime_ms": int((time.perf_counter() - flow_started) * 1000)},
                progress={"current_step": "review_required", "elapsed_ms": int((time.perf_counter() - flow_started) * 1000), "last_heartbeat_at": utcnow().isoformat()},
                upload_diagnostics=upload_diagnostics,
                select_diagnostics=select_diagnostics,
                exceptions=exceptions,
                modal_closed=False,
                **debug.result_kwargs(page),
            )
        apply_button = _jobserve_apply_button(context)
        if apply_button.count() == 0:
            debug.final_error = "Submit button not found in JobServe apply form."
            debug.html("submit_button_not_found", context)
            raise RuntimeError(debug.final_error)
        submit_started = time.perf_counter()
        debug.step(
            "jobserve_about_to_submit",
            jobserve_flow_diagnostics=flow,
            submit_guard={
                "email_filled": flow.get("email_filled"),
                "email_value": _jobserve_email_field_value(context) or flow.get("email_value"),
                "confirmation_checkbox_checked": flow.get("confirmation_email_checked"),
                "working_status_selected": flow.get("uk_status_selected"),
                "working_status_value": flow.get("uk_status_value"),
                "cv_uploaded": uploaded_cv,
                "identity_verified": True,
                "final_apply_click_enabled": True,
                "intended_job": job_context,
                "verified_job": verified_identity,
                "modal_job": modal_identity,
                "modal_title": modal_identity.get("title") or "",
            },
        )
        debug.screenshot("before_final_submit")
        debug.html("before_final_submit", context)
        _click_locator_resilient(page, apply_button)
        flow["final_apply_clicked"] = True
        flow["first_apply_clicked"] = True
        _report_jobserve_step(progress_callback, "final_apply_clicked", succeeded=True)
        debug.step("jobserve_final_apply_clicked", jobserve_flow_diagnostics=flow)
        debug.step("jobserve_first_apply_clicked", jobserve_flow_diagnostics=flow)
        try:
            confirmation_text = _wait_for_jobserve_submission_success(page, browser)
            timing_diagnostics["submit_wait_ms"] = int((time.perf_counter() - submit_started) * 1000)
            flow["submitted_confirmation_detected"] = True
            flow["confirmation_text"] = confirmation_text
            if re.search(r"already applied|already submitted|you have applied", confirmation_text, flags=re.I):
                flow["already_applied_detected"] = True
            flow["submitted_identity"] = _jobserve_modal_identity(page)
            submitted = True
            status = "submitted"
            _report_jobserve_step(progress_callback, "submitted_message_seen", succeeded=True, confirmation_text=confirmation_text)
            debug.step("jobserve_submitted_message_seen", jobserve_flow_diagnostics=flow)
            debug.screenshot("after_final_submit")
            debug.html("after_final_submit", page)
        except Exception as exc:  # noqa: BLE001
            debug.final_error = "JobServe submission confirmation not detected."
            exceptions.append(_exception_payload("confirmation_detection", exc, jobserve_flow_diagnostics=dict(flow)))
            debug.html("confirmation_not_detected", page)
            raise RuntimeError(debug.final_error) from exc
        flow["account_toggles_turned_off"] = _disable_jobserve_account_options(page, warnings)
        flow["registration_toggle_disabled"] = any("register a Job Seeker account" in item for item in flow["account_toggles_turned_off"])
        _report_jobserve_step(progress_callback, "account_toggle_disabled", succeeded=flow["registration_toggle_disabled"], disabled=flow["account_toggles_turned_off"])
        debug.step("jobserve_registration_toggle_disabled", jobserve_flow_diagnostics=flow)
        debug.screenshot("registration_toggles_disabled")
        flow["modal_closed"] = _close_modal(page)
        _report_jobserve_step(progress_callback, "modal_closed", succeeded=flow["modal_closed"])
        debug.step("jobserve_modal_closed", jobserve_flow_diagnostics=flow)
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
        submitted_job_title=str((flow.get("submitted_identity") or flow.get("verified_detail_panel_identity") or {}).get("title") or "") or None,
        submitted_job_company=str((flow.get("submitted_identity") or flow.get("verified_detail_panel_identity") or {}).get("company") or "") or None,
        submitted_job_reference=str((flow.get("submitted_identity") or flow.get("verified_detail_panel_identity") or {}).get("reference") or "") or None,
        submitted_job_external_id=str((flow.get("submitted_identity") or flow.get("verified_detail_panel_identity") or {}).get("reference") or "") or None,
        confirmation_text=str(flow.get("confirmation_text") or "") or None,
        registration_toggle_disabled=bool(flow.get("registration_toggle_disabled")),
        modal_closed=bool(flow.get("modal_closed")),
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
    screenshot_callback: Callable[[str], None] | None = None,
) -> bool:
    prefs = flow["search_defaults"]
    controls = flow["search_controls"]
    controls["keywords"] = _fill_first_label_or_selector(page, [r"keywords?", r"what"], ['input[name*="keyword" i]', 'input[id*="keyword" i]', "input[type=search]"], prefs["keywords"])
    _report_jobserve_step(step_callback, "jobserve_search_keyword_filled", value=prefs["keywords"], succeeded=controls["keywords"])
    controls["location"] = _fill_first_label_or_selector(page, [r"location", r"where"], ['input[name*="location" i]', 'input[id*="location" i]'], prefs["location"])
    _report_jobserve_step(step_callback, "jobserve_search_location_filled", value=prefs["location"], succeeded=controls["location"])
    _jobserve_dropdown_screenshot(screenshot_callback, "before_distance_dropdown_selection")
    controls["distance"] = jobserve_click_dropdown_option(
        page,
        {"labels": [r"distance", r"miles"], "selectors": ['select[name*="distance" i]', 'select[id*="distance" i]', 'select[name*="rad" i]', 'select[id*="rad" i]']},
        prefs["distance"],
        field_name="Search distance",
        diagnostics=select_diagnostics,
        step_callback=step_callback,
        step_prefix="jobserve_search_distance",
    )
    _report_jobserve_step(step_callback, "jobserve_search_distance_selected", value=prefs["distance"], succeeded=controls["distance"])
    _jobserve_dropdown_screenshot(screenshot_callback, "after_distance_dropdown_selection")
    controls["posted_within"] = jobserve_click_dropdown_option(
        page,
        {"labels": [r"posted", r"date"], "selectors": ['select[name*="posted" i]', 'select[id*="posted" i]', 'select[name*="age" i]']},
        prefs["posted_within"],
        field_name="Posted within",
        diagnostics=select_diagnostics,
        step_callback=step_callback,
        step_prefix="jobserve_search_posted",
    )
    _report_jobserve_step(step_callback, "jobserve_search_posted_selected", value=prefs["posted_within"], succeeded=controls["posted_within"])
    _jobserve_dropdown_screenshot(screenshot_callback, "after_posted_dropdown_selection")
    controls["job_type"] = jobserve_click_dropdown_option(
        page,
        {"labels": [r"job type", r"type"], "selectors": ['select[name*="type" i]', 'select[id*="type" i]']},
        prefs["job_type"],
        field_name="Job type",
        diagnostics=select_diagnostics,
        step_callback=step_callback,
        step_prefix="jobserve_search_job_type",
    )
    _report_jobserve_step(step_callback, "jobserve_search_job_type_selected", value=prefs["job_type"], succeeded=controls["job_type"])
    _jobserve_dropdown_screenshot(screenshot_callback, "after_job_type_dropdown_selection")
    remote_diagnostic: dict[str, Any] = {}
    controls["remote_only_unchecked"] = _set_checkbox_by_label(page, [r"only show jobs with remote working", r"remote only", r"remote working"], checked=False, diagnostic=remote_diagnostic)
    controls["remote_only_diagnostic"] = remote_diagnostic
    _report_jobserve_step(step_callback, "jobserve_search_remote_only_unchecked", succeeded=controls["remote_only_unchecked"], **remote_diagnostic)
    controls["industries_select_all"] = _select_all_jobserve_industries(page, select_diagnostics, step_callback=step_callback)
    _report_jobserve_step(step_callback, "jobserve_search_industries_selected", value="Select All", succeeded=controls["industries_select_all"])
    _jobserve_dropdown_screenshot(screenshot_callback, "after_industries_dropdown_selection")
    return bool(controls["keywords"] and controls["location"])


def _report_jobserve_step(step_callback: Callable[[str, dict[str, Any]], None] | None, step: str, **payload: Any) -> None:
    logger.info("%s %s", step, payload)
    if step_callback is not None:
        step_callback(step, payload)


def _report_stage(step_callback: Callable[[str, dict[str, Any]], None] | None, stage: str, state: str, **payload: Any) -> None:
    _report_jobserve_step(step_callback, f"{stage}_{state}", **payload)


def _jobserve_dropdown_screenshot(screenshot_callback: Callable[[str], None] | None, name: str) -> None:
    if screenshot_callback is not None:
        screenshot_callback(name)


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


def jobserve_click_dropdown_option(
    page_or_frame,
    dropdown_label_or_locator,
    option_text: str,
    *,
    field_name: str | None = None,
    diagnostics: list[dict[str, Any]] | None = None,
    step_callback: Callable[[str, dict[str, Any]], None] | None = None,
    step_prefix: str | None = None,
) -> bool:
    diagnostic: dict[str, Any] = {
        "field": field_name or option_text,
        "target": option_text,
        "dropdown_clicked": False,
        "visible_options_found": [],
        "selected_option": None,
        "fallback_used": None,
        "success": False,
        "failure_reason": None,
        "detected_selects": _jobserve_detect_selects(page_or_frame),
        "initial_selected_text": None,
        "initial_selected_value": None,
        "requested_option": option_text,
        "native_select_worked": False,
        "click_option_worked": False,
        "final_selected_text": None,
        "final_selected_value": None,
    }
    locators = _jobserve_dropdown_locators(page_or_frame, dropdown_label_or_locator)
    failures: list[str] = []
    normalized_target = _normalize_select_text(option_text)

    for locator in locators:
        state = _jobserve_selected_state(locator)
        if state:
            diagnostic["initial_selected_text"] = diagnostic["initial_selected_text"] or state.get("text")
            diagnostic["initial_selected_value"] = diagnostic["initial_selected_value"] or state.get("value")
            if _jobserve_selected_matches(state, option_text):
                _report_jobserve_step(step_callback, f"{step_prefix}_already_selected" if step_prefix else "jobserve_dropdown_already_selected", field=diagnostic["field"], option=option_text)
                _jobserve_dropdown_success(diagnostic, diagnostics, option_text, "already_selected", locator=locator)
                return True

        native_selected = _jobserve_native_select_fallback(locator, option_text, diagnostic)
        if native_selected:
            diagnostic["native_select_worked"] = True
            _jobserve_dropdown_success(diagnostic, diagnostics, option_text, "native_select", locator=locator)
            return True

        try:
            locator.click(timeout=1500)
            diagnostic["dropdown_clicked"] = True
            _report_jobserve_step(step_callback, f"{step_prefix}_dropdown_opened" if step_prefix else "jobserve_dropdown_opened", field=diagnostic["field"], target=option_text)
            page_or_frame.wait_for_timeout(250)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"click failed: {exc}")
            native_selected = _jobserve_native_select_fallback(locator, option_text, diagnostic)
            if native_selected:
                _jobserve_dropdown_success(diagnostic, diagnostics, option_text, "native_select_before_click")
                return True
            continue

        visible_options = _jobserve_visible_option_texts(page_or_frame)
        diagnostic["visible_options_found"] = visible_options[:80]
        option_locator = _jobserve_visible_option_locator(page_or_frame, option_text)
        if option_locator is not None:
            try:
                option_locator.click(timeout=2000)
                _report_jobserve_step(step_callback, f"{step_prefix}_option_clicked" if step_prefix else "jobserve_dropdown_option_clicked", field=diagnostic["field"], option=option_text)
                diagnostic["click_option_worked"] = True
                _jobserve_dropdown_success(diagnostic, diagnostics, option_text, "visible_text", locator=locator)
                return True
            except Exception as exc:  # noqa: BLE001
                failures.append(f"visible option click failed: {exc}")

        normalized_option = _jobserve_visible_option_locator(page_or_frame, normalized_target, normalized=True)
        if normalized_option is not None:
            try:
                normalized_option.click(timeout=2000)
                _report_jobserve_step(step_callback, f"{step_prefix}_option_clicked" if step_prefix else "jobserve_dropdown_option_clicked", field=diagnostic["field"], option=option_text)
                diagnostic["click_option_worked"] = True
                _jobserve_dropdown_success(diagnostic, diagnostics, option_text, "normalized_visible_text", locator=locator)
                return True
            except Exception as exc:  # noqa: BLE001
                failures.append(f"normalized visible option click failed: {exc}")

        native_selected = _jobserve_native_select_fallback(locator, option_text, diagnostic)
        if native_selected:
            diagnostic["native_select_worked"] = True
            _jobserve_dropdown_success(diagnostic, diagnostics, option_text, "native_select_after_click", locator=locator)
            return True

        if _jobserve_keyboard_dropdown_fallback(locator, option_text, diagnostic):
            _jobserve_dropdown_success(diagnostic, diagnostics, option_text, "keyboard", locator=locator)
            return True

        if _jobserve_js_dropdown_fallback(locator, option_text, diagnostic):
            _jobserve_dropdown_success(diagnostic, diagnostics, option_text, "js_change", locator=locator)
            return True

    diagnostic["failure_reason"] = "; ".join(failures[-5:]) or "Dropdown option could not be selected."
    if diagnostics is not None:
        diagnostics.append(diagnostic)
    return False


def _jobserve_dropdown_success(diagnostic: dict[str, Any], diagnostics: list[dict[str, Any]] | None, option_text: str, fallback_used: str, *, locator=None) -> None:
    final_state = _jobserve_selected_state(locator) if locator is not None else None
    if final_state:
        diagnostic["final_selected_text"] = final_state.get("text")
        diagnostic["final_selected_value"] = final_state.get("value")
    diagnostic["selected_option"] = option_text
    diagnostic["fallback_used"] = fallback_used
    diagnostic["success"] = True
    diagnostic["failure_reason"] = None
    if diagnostics is not None:
        diagnostics.append(diagnostic)


def _jobserve_dropdown_locators(page_or_frame, dropdown_label_or_locator) -> list[Any]:
    if hasattr(dropdown_label_or_locator, "click"):
        return [dropdown_label_or_locator]
    labels: list[str] = []
    selectors: list[str] = []
    if isinstance(dropdown_label_or_locator, dict):
        labels = list(dropdown_label_or_locator.get("labels") or [])
        selectors = list(dropdown_label_or_locator.get("selectors") or [])
    elif isinstance(dropdown_label_or_locator, str):
        labels = [dropdown_label_or_locator]
    elif isinstance(dropdown_label_or_locator, list):
        labels = [str(item) for item in dropdown_label_or_locator]
    locators = []
    for pattern in labels:
        locators.extend(
            [
                page_or_frame.get_by_label(re.compile(pattern, re.I)).first,
                page_or_frame.get_by_text(re.compile(pattern, re.I)).first,
            ]
        )
    locators.extend(page_or_frame.locator(selector).first for selector in selectors)
    return locators


def _jobserve_detect_selects(page_or_frame) -> list[dict[str, Any]]:
    try:
        return page_or_frame.evaluate(
            """() => Array.from(document.querySelectorAll('select')).map((select, index) => {
                const selected = select.options[select.selectedIndex];
                return {
                    index,
                    name: select.getAttribute('name') || '',
                    id: select.getAttribute('id') || '',
                    aria_label: select.getAttribute('aria-label') || '',
                    selected_text: selected ? (selected.label || selected.textContent || '').trim() : '',
                    selected_value: select.value || '',
                    options: Array.from(select.options || []).map((option) => (option.label || option.textContent || '').trim()).slice(0, 20)
                };
            })""",
            timeout=1000,
        )
    except Exception:  # noqa: BLE001
        return []


def _jobserve_selected_state(locator) -> dict[str, str] | None:
    if locator is None:
        return None
    try:
        return locator.evaluate(
            """element => {
                if (!element || !element.matches?.('select')) return null;
                const selected = element.options[element.selectedIndex];
                return {
                    text: selected ? (selected.label || selected.textContent || '').trim() : '',
                    value: element.value || ''
                };
            }""",
            timeout=500,
        )
    except Exception:  # noqa: BLE001
        return None


def _jobserve_selected_matches(state: dict[str, str], option_text: str) -> bool:
    target = _normalize_select_text(option_text)
    return any(_normalize_select_text(str(state.get(key) or "")) == target for key in ["text", "value"])


def _jobserve_visible_option_texts(page_or_frame) -> list[str]:
    try:
        return page_or_frame.evaluate(
            """() => Array.from(document.querySelectorAll('[role=option], [role=menuitem], li, a, button, option, .select2-results__option, .dropdown-item'))
                .filter((el) => {
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
                })
                .map((el) => (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' '))
                .filter(Boolean)
                .slice(0, 120)"""
        )
    except Exception:  # noqa: BLE001
        return []


def _jobserve_visible_option_locator(page_or_frame, option_text: str, *, normalized: bool = False):
    if not normalized:
        for locator in [
            page_or_frame.get_by_role("option", name=re.compile(f"^{re.escape(option_text)}$", re.I)).first,
            page_or_frame.get_by_role("menuitem", name=re.compile(f"^{re.escape(option_text)}$", re.I)).first,
            page_or_frame.get_by_text(option_text, exact=True).last,
            page_or_frame.get_by_text(re.compile(f"^{re.escape(option_text)}$", re.I)).last,
        ]:
            try:
                if locator.is_visible(timeout=500):
                    return locator
            except Exception:  # noqa: BLE001
                continue
    normalized_target = _normalize_select_text(option_text)
    for text in _jobserve_visible_option_texts(page_or_frame):
        if _normalize_select_text(text) == normalized_target:
            locator = page_or_frame.get_by_text(text, exact=True).last
            try:
                if locator.is_visible(timeout=500):
                    return locator
            except Exception:  # noqa: BLE001
                continue
    return None


def _jobserve_native_select_fallback(locator, option_text: str, diagnostic: dict[str, Any]) -> bool:
    before = len(diagnostic.get("available_options") or [])
    selected = _select_locator_option(locator, option_text, field_name=str(diagnostic["field"]), label_pattern="jobserve_dropdown_native", diagnostics=None)
    if selected:
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
            if len(options) > before:
                diagnostic["available_options"] = options
        except Exception:  # noqa: BLE001
            pass
    return selected


def _jobserve_keyboard_dropdown_fallback(locator, option_text: str, diagnostic: dict[str, Any]) -> bool:
    try:
        locator.click(timeout=1000)
        locator.press_sequentially(option_text, timeout=1500)
        locator.press("Enter", timeout=1000)
        return True
    except Exception as exc:  # noqa: BLE001
        diagnostic["failure_reason"] = f"keyboard fallback failed: {exc}"
        return False


def _jobserve_js_dropdown_fallback(locator, option_text: str, diagnostic: dict[str, Any]) -> bool:
    try:
        return bool(
            locator.evaluate(
                """(element, target) => {
                    const normalize = (value) => String(value || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
                    const options = Array.from(element.options || []);
                    const wanted = normalize(target);
                    const option = options.find((item) => item.textContent === target || normalize(item.textContent) === wanted || normalize(item.textContent).includes(wanted));
                    if (!option) return false;
                    element.value = option.value;
                    element.dispatchEvent(new Event('input', { bubbles: true }));
                    element.dispatchEvent(new Event('change', { bubbles: true }));
                    return true;
                }""",
                option_text,
                timeout=1000,
            )
        )
    except Exception as exc:  # noqa: BLE001
        diagnostic["failure_reason"] = f"js fallback failed: {exc}"
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


def _set_checkbox_by_label(page, labels: list[str], *, checked: bool, diagnostic: dict[str, Any] | None = None, click_unknown: bool = False) -> bool:
    details: dict[str, Any] = {
        "checkbox_found": False,
        "initial_checked": None,
        "clicked": False,
        "final_checked": None,
        "result": "not_found",
    }
    failures: list[str] = []
    for pattern in labels:
        for control in _checkbox_candidates_by_label(page, pattern):
            try:
                current = _checkbox_checked_state(control)
                if current is None:
                    if click_unknown:
                        details["checkbox_found"] = True
                        _click_checkbox_box(control)
                        details["clicked"] = True
                        details["result"] = "unknown_clicked_once"
                        details["final_checked"] = _checkbox_checked_state(control)
                        if diagnostic is not None:
                            diagnostic.update(details)
                        return True
                    failures.append(f"{pattern}: state unknown")
                    continue
                details["checkbox_found"] = True
                details["initial_checked"] = current
                if current == checked:
                    details["final_checked"] = current
                    details["result"] = "already_checked" if checked else "already_unchecked"
                    if diagnostic is not None:
                        diagnostic.update(details)
                    return True
                _click_checkbox_box(control)
                details["clicked"] = True
                final = _checkbox_checked_state(control)
                details["final_checked"] = final
                if final == checked:
                    details["result"] = "checked_after_click" if checked else "unchecked_after_click"
                    if diagnostic is not None:
                        diagnostic.update(details)
                    return True
                failures.append(f"{pattern}: final state {final!r}")
            except Exception:  # noqa: BLE001
                failures.append(pattern)
                continue
    details["failure_reason"] = "; ".join(failures[-5:]) if failures else "checkbox not found"
    if diagnostic is not None:
        diagnostic.update(details)
    return False


def _checkbox_candidates_by_label(page, pattern: str) -> list[Any]:
    candidates = []
    try:
        candidates.extend(page.get_by_label(re.compile(pattern, re.I)).all())
    except Exception:  # noqa: BLE001
        pass
    try:
        label = page.get_by_text(re.compile(pattern, re.I)).first
        label_for = label.get_attribute("for", timeout=500)
        if label_for:
            candidates.append(page.locator(f"#{label_for}").first)
        candidates.append(label.locator("input[type=checkbox]").first)
        candidates.append(label.locator("xpath=ancestor-or-self::*[self::label or @role='checkbox' or contains(@class, 'checkbox')][1]").first)
    except Exception:  # noqa: BLE001
        pass
    try:
        candidates.append(page.locator('input[type=checkbox][name*="remote" i], input[type=checkbox][id*="remote" i]').first)
        candidates.append(page.locator('[role=checkbox][aria-label*="remote" i], [class*="checkbox" i][class*="remote" i], [id*="remote" i][class*="checkbox" i]').first)
    except Exception:  # noqa: BLE001
        pass
    return candidates


def _checkbox_checked_state(locator) -> bool | None:
    try:
        return bool(locator.is_checked(timeout=500))
    except Exception:  # noqa: BLE001
        pass
    try:
        return bool(locator.is_checked())
    except Exception:  # noqa: BLE001
        pass
    try:
        aria_checked = locator.get_attribute("aria-checked", timeout=500)
        if aria_checked is not None:
            return aria_checked.lower() == "true"
    except Exception:  # noqa: BLE001
        pass
    try:
        return locator.evaluate(
            """element => {
                const target = element.matches?.('input[type=checkbox]') ? element : element.querySelector?.('input[type=checkbox]');
                if (target) return Boolean(target.checked);
                const aria = element.getAttribute?.('aria-checked');
                if (aria !== null && aria !== undefined) return String(aria).toLowerCase() === 'true';
                const className = String(element.className || '').toLowerCase();
                if (className.includes('unchecked')) return false;
                if (className.includes('checked') || className.includes('selected') || className.includes('active')) return true;
                return null;
            }""",
            timeout=500,
        )
    except Exception:  # noqa: BLE001
        return None


def _click_checkbox_box(locator) -> None:
    try:
        locator.uncheck()
        return
    except Exception:  # noqa: BLE001
        pass
    try:
        box = locator.locator("input[type=checkbox]").first
        box.click(timeout=1000, position={"x": 6, "y": 6})
        return
    except Exception:  # noqa: BLE001
        pass
    locator.click(timeout=1000, position={"x": 6, "y": 6})


def _select_all_jobserve_industries(
    page,
    diagnostics: list[dict[str, Any]] | None = None,
    *,
    step_callback: Callable[[str, dict[str, Any]], None] | None = None,
) -> bool:
    return jobserve_click_dropdown_option(
        page,
        {"labels": [r"industr(y|ies)", r"sector"], "selectors": ['select[name*="industry" i]', 'select[id*="industry" i]', 'select[name*="ind" i]', '[id*="industry" i]', '[class*="industry" i]']},
        "Select All",
        field_name="Industries",
        diagnostics=diagnostics,
        step_callback=step_callback,
        step_prefix="jobserve_search_industries",
    )


def _click_jobserve_search(page, diagnostics: dict[str, Any] | None = None) -> bool:
    details: dict[str, Any] = {
        "current_url": _safe_url(page),
        "page_title": _safe_title(page),
        "visible_buttons": _visible_button_inventory(page),
        "input_submit_buttons": _input_submit_inventory(page),
        "search_links": _search_link_inventory(page),
        "selector_used": None,
        "button_bounding_box": None,
        "button_enabled": None,
        "button_visible": None,
        "click_strategy": None,
        "click_error": None,
        "results_wait": {},
        "final_url": None,
        "final_title": None,
    }
    start_url = _safe_url(page)
    candidates = [
        ("role_button_search", page.get_by_role("button", name=re.compile(r"^\\s*search\\s*$", re.I)).first),
        ("input_submit_value_search", page.locator('input[type="submit" i][value="Search" i], input[type="button" i][value="Search" i]').first),
        ("button_text_search", page.locator('button:has-text("Search"), input:has-text("Search")').first),
        ("search_button_near_reset", page.locator('form:has-text("Reset") button:has-text("Search"), form:has-text("Reset") input[value="Search" i]').first),
        ("blue_search_button", page.locator('form button[class*="blue" i], form input[class*="blue" i], form .btn-primary, form [class*="search" i]').first),
        ("text_search", page.get_by_text(re.compile(r"^\\s*search\\s*$", re.I)).last),
    ]
    errors: list[str] = []
    for name, locator in candidates:
        clicked, state = _click_jobserve_search_candidate(page, locator)
        details["selector_used"] = name
        details["button_bounding_box"] = state.get("bounding_box")
        details["button_enabled"] = state.get("enabled")
        details["button_visible"] = state.get("visible")
        details["click_strategy"] = state.get("strategy")
        details["click_error"] = state.get("error")
        if clicked:
            if _wait_for_jobserve_results(page, start_url, details["results_wait"]):
                details["final_url"] = _safe_url(page)
                details["final_title"] = _safe_title(page)
                if diagnostics is not None:
                    diagnostics.update(details)
                return True
            errors.append(f"{name}: clicked but results did not load")
        else:
            errors.append(f"{name}: {state.get('error') or 'not clickable'}")

    enter_clicked, enter_error = _press_enter_to_submit_jobserve_search(page)
    details["selector_used"] = "enter_key_fallback"
    details["click_strategy"] = "press_enter"
    details["click_error"] = enter_error
    if enter_clicked and _wait_for_jobserve_results(page, start_url, details["results_wait"]):
        details["final_url"] = _safe_url(page)
        details["final_title"] = _safe_title(page)
        if diagnostics is not None:
            diagnostics.update(details)
        return True
    errors.append(f"enter_key_fallback: {enter_error or 'results did not load'}")
    details["failure_reason"] = "; ".join(errors[-8:])
    details["final_url"] = _safe_url(page)
    details["final_title"] = _safe_title(page)
    if diagnostics is not None:
        diagnostics.update(details)
    return False


def _click_jobserve_search_candidate(page, locator) -> tuple[bool, dict[str, Any]]:
    state: dict[str, Any] = {"visible": None, "enabled": None, "bounding_box": None, "strategy": None, "error": None}
    try:
        state["visible"] = locator.is_visible(timeout=800)
        state["enabled"] = locator.is_enabled(timeout=800)
        state["bounding_box"] = locator.bounding_box(timeout=800)
    except Exception as exc:  # noqa: BLE001
        state["error"] = f"inspect failed: {exc}"
        return False, state
    for strategy, action in [
        ("normal_click", lambda: locator.click(timeout=2500)),
        ("force_click", lambda: locator.click(timeout=2500, force=True)),
        ("js_click", lambda: locator.evaluate("element => element.click()", timeout=1000)),
        ("coordinate_click", lambda: _click_locator_center(page, locator)),
    ]:
        try:
            action()
            state["strategy"] = strategy
            state["error"] = None
            return True, state
        except Exception as exc:  # noqa: BLE001
            state["error"] = f"{strategy}: {exc}"
    return False, state


def _click_locator_center(page, locator) -> None:
    box = locator.bounding_box(timeout=1000)
    if not box:
        raise RuntimeError("No bounding box for coordinate click")
    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)


def _click_locator_resilient(page, locator) -> None:
    failures: list[str] = []
    for action in [
        lambda: locator.click(timeout=settings.playwright_step_timeout_ms),
        lambda: locator.click(timeout=settings.playwright_step_timeout_ms, force=True),
        lambda: locator.evaluate("element => element.click()", timeout=1000),
        lambda: _click_locator_center(page, locator),
    ]:
        try:
            action()
            return
        except Exception as exc:  # noqa: BLE001
            failures.append(str(exc))
    raise RuntimeError("; ".join(failures[-3:]))


def _press_enter_to_submit_jobserve_search(page) -> tuple[bool, str | None]:
    for locator in [
        page.locator('input[name*="keyword" i], input[id*="keyword" i], input[type=search]').first,
        page.locator('input[name*="location" i], input[id*="location" i]').first,
        page.locator("form input").first,
    ]:
        try:
            locator.press("Enter", timeout=1500)
            return True, None
        except Exception as exc:  # noqa: BLE001
            last = str(exc)
            continue
    return False, last if "last" in locals() else "No searchable input accepted Enter"


def _wait_for_jobserve_results(page, start_url: str | None, diagnostics: dict[str, Any]) -> bool:
    checks = {
        "url_changed": False,
        "jobsearch_url": False,
        "result_count_text": False,
        "job_entries": False,
        "job_list_column": False,
    }
    try:
        page.wait_for_load_state("networkidle", timeout=12000)
    except Exception as exc:  # noqa: BLE001
        diagnostics["networkidle_error"] = str(exc)
    try:
        page.wait_for_timeout(800)
    except Exception:  # noqa: BLE001
        pass
    current_url = _safe_url(page)
    checks["url_changed"] = bool(start_url and current_url and current_url != start_url)
    checks["jobsearch_url"] = bool(current_url and re.search(r"JobSearch|Job-Search|shid=", current_url, re.I))
    try:
        body_text = page.locator("body").inner_text(timeout=1500)
        checks["result_count_text"] = bool(re.search(r"\\b\\d+\\s+(jobs?|results?)\\b|jobs? found|results? found", body_text, re.I))
    except Exception as exc:  # noqa: BLE001
        diagnostics["body_text_error"] = str(exc)
    try:
        checks["job_entries"] = page.locator('a[href*="/job/"], a[href*="/gb/en/job"], article, .job, .job-result, .JobResult, [data-jobid]').count() > 0
    except Exception as exc:  # noqa: BLE001
        diagnostics["job_entries_error"] = str(exc)
    try:
        checks["job_list_column"] = page.locator('[id*="job" i], [class*="job" i], [id*="result" i], [class*="result" i]').count() > 0
    except Exception as exc:  # noqa: BLE001
        diagnostics["job_list_error"] = str(exc)
    diagnostics.update(checks)
    diagnostics["final_url"] = current_url
    diagnostics["final_title"] = _safe_title(page)
    return any(checks.values())


def _visible_button_inventory(page) -> list[dict[str, Any]]:
    try:
        return page.evaluate(
            """() => Array.from(document.querySelectorAll('button, input[type=button], input[type=submit], [role=button]'))
                .filter((el) => {
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
                })
                .map((el, index) => ({ index, tag: el.tagName, text: (el.innerText || el.value || el.getAttribute('aria-label') || '').trim(), id: el.id || '', name: el.getAttribute('name') || '', type: el.getAttribute('type') || '', className: String(el.className || ''), rect: (() => { const r = el.getBoundingClientRect(); return { x: r.x, y: r.y, width: r.width, height: r.height }; })() }))
                .slice(0, 60)""",
            timeout=1000,
        )
    except Exception:  # noqa: BLE001
        return []


def _input_submit_inventory(page) -> list[dict[str, Any]]:
    try:
        return page.evaluate(
            """() => Array.from(document.querySelectorAll('input[type=submit], input[type=button]'))
                .map((el, index) => ({ index, value: el.value || '', id: el.id || '', name: el.name || '', className: String(el.className || ''), disabled: Boolean(el.disabled) }))
                .slice(0, 40)""",
            timeout=1000,
        )
    except Exception:  # noqa: BLE001
        return []


def _search_link_inventory(page) -> list[dict[str, Any]]:
    try:
        return page.evaluate(
            """() => Array.from(document.querySelectorAll('a[href]'))
                .filter((el) => /search/i.test((el.innerText || el.textContent || '').trim()))
                .map((el, index) => ({ index, text: (el.innerText || el.textContent || '').trim(), href: el.href || '', id: el.id || '', className: String(el.className || '') }))
                .slice(0, 30)""",
            timeout=1000,
        )
    except Exception:  # noqa: BLE001
        return []


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


def _verify_or_select_intended_jobserve_result(page, browser, job_context: dict[str, Any], flow: dict[str, Any]) -> dict[str, Any] | None:
    if not _jobserve_target_has_identity(job_context):
        flow["blocked_reason"] = "Intended JobServe job identity missing"
        flow["target_job_match_candidates"] = _jobserve_result_candidates(page)[:10]
        return None
    candidates = _rank_jobserve_candidates(_jobserve_result_candidates(page), job_context)
    flow["target_job_match_candidates"] = candidates[:10]
    auto_identity = _jobserve_detail_panel_identity(page)
    flow["auto_selected_result_identity"] = auto_identity
    if auto_identity and _jobserve_identity_matches(auto_identity, job_context):
        flow["auto_selected_matched"] = True
        flow["first_job_selected"] = True
        flow["selected_result_identity"] = auto_identity
        flow["verified_detail_panel_identity"] = auto_identity
        flow["identity_check_result"] = "matched_auto_selected"
        return auto_identity
    flow["auto_selected_matched"] = False
    if not candidates or int(candidates[0].get("score") or 0) <= 0:
        flow["blocked_reason"] = "Intended JobServe job not found in results"
        return None
    selected = candidates[0]
    href = selected.get("href")
    try:
        if href:
            page.locator(f'a[href="{href}"]').first.click(timeout=5000)
        else:
            page.get_by_text(str(selected.get("title") or selected.get("text") or ""), exact=False).first.click(timeout=5000)
        page.wait_for_timeout(1200)
    except Exception as exc:  # noqa: BLE001
        flow["blocked_reason"] = f"Could not click intended JobServe result: {exc}"
        return None
    detail_identity = _jobserve_detail_panel_identity(page) or selected
    flow["selected_result_identity"] = selected
    flow["verified_detail_panel_identity"] = detail_identity
    if detail_identity and not _jobserve_identity_matches(detail_identity, job_context):
        flow["blocked_reason"] = "Selected JobServe detail panel does not match intended job"
        flow["identity_check_result"] = "mismatch"
        return None
    if not detail_identity and not _jobserve_identity_matches(selected, job_context):
        flow["blocked_reason"] = "Selected JobServe result does not match intended job"
        flow["identity_check_result"] = "mismatch"
        return None
    flow["identity_check_result"] = "matched_selected_result"
    return detail_identity


def _jobserve_detail_panel_identity(page) -> dict[str, Any] | None:
    try:
        identity = page.evaluate(
            """() => {
                const visible = (el) => {
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
                };
                const candidates = Array.from(document.querySelectorAll('[id*="detail" i], [class*="detail" i], [id*="job" i], [class*="job" i], main, body')).filter(visible);
                const panel = candidates.find((el) => /apply/i.test(el.innerText || el.textContent || '')) || document.body;
                const text = (panel.innerText || panel.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 3000);
                const link = panel.querySelector('a[href*="/job/"], a[href*="jobserve.com"]');
                let heading = panel.querySelector('h1,h2,h3,.job-title,.title,[class*="title" i]');
                if (!heading) {
                    const applyButton = Array.from(panel.querySelectorAll('button,a,input[type=button],input[type=submit]')).find((el) => /^apply(\\s+now)?$/i.test(((el.innerText || el.value || el.textContent || '') + '').trim()));
                    const applyParent = applyButton ? applyButton.closest('section,article,div,main') : null;
                    heading = applyParent ? applyParent.querySelector('h1,h2,h3,.job-title,.title,[class*="title" i]') : null;
                }
                if (!heading) {
                    heading = document.querySelector('[aria-selected="true"] h1,[aria-selected="true"] h2,[aria-selected="true"] h3,[aria-selected="true"] .job-title,[aria-selected="true"] [class*="title" i], .selected h1,.selected h2,.selected h3,.selected .job-title,.active h1,.active h2,.active h3,.active .job-title');
                }
                const company = panel.querySelector('.company,.recruiter,.employer,[class*="company" i],[class*="recruiter" i]');
                const refMatch = text.match(/(?:ref(?:erence)?\\s*[:#]?\\s*)([A-Z0-9-]{3,})/i);
                const salaryMatch = text.match(/(?:£|GBP|salary)\\s?[^\\n\\r]{0,80}/i);
                const locationNode = panel.querySelector('.location,[class*="location" i],[class*="loc" i]');
                return {
                    text,
                    title: heading ? heading.innerText.trim() : '',
                    company: company ? company.innerText.trim() : '',
                    href: link ? link.href : location.href,
                    reference: refMatch ? refMatch[1] : '',
                    location: locationNode ? locationNode.innerText.trim() : '',
                    salary: salaryMatch ? salaryMatch[0].trim() : ''
                };
            }""",
            timeout=1500,
        )
        return identity if identity and any(identity.get(key) for key in ["text", "title", "href", "reference"]) else None
    except Exception:  # noqa: BLE001
        return None


def _jobserve_modal_identity(context) -> dict[str, Any]:
    try:
        text = context.locator("body").inner_text(timeout=1500)
    except Exception:  # noqa: BLE001
        text = ""
    title_match = re.search(r"(?:Job Title|Position|Role)\s*:?\s*([^\n\r]+)", text, re.I)
    ref_match = re.search(r"(?:ref(?:erence)?|job id)\s*[:#]?\s*([A-Z0-9-]{3,})", text, re.I)
    return {
        "text": re.sub(r"\s+", " ", text).strip()[:2000],
        "title": title_match.group(1).strip() if title_match else "",
        "reference": ref_match.group(1).strip() if ref_match else "",
        "href": _safe_url(context),
    }


def _jobserve_identity_clear_mismatch(modal_identity: dict[str, Any], verified_identity: dict[str, Any]) -> bool:
    if not modal_identity or not verified_identity:
        return False
    modal_ref = _normalize_select_text(str(modal_identity.get("reference") or ""))
    verified_ref = _normalize_select_text(str(verified_identity.get("reference") or ""))
    if modal_ref and verified_ref and modal_ref != verified_ref:
        return True
    modal_title = _normalize_select_text(str(modal_identity.get("title") or ""))
    verified_title = _normalize_select_text(str(verified_identity.get("title") or ""))
    return bool(modal_title and verified_title and modal_title not in verified_title and verified_title not in modal_title)


def _jobserve_target_has_identity(target: dict[str, Any]) -> bool:
    return any(str(target.get(key) or "").strip() for key in ["source_job_id", "original_external_id", "canonical_url", "title", "original_title", "company_name", "original_company"])


def _jobserve_use_current_selected_job_as_intended(page, flow: dict[str, Any]) -> dict[str, Any]:
    current_identity = _jobserve_detail_panel_identity(page)
    if not current_identity:
        current_identity = _jobserve_visible_job_fallback_identity(page)
    flow["auto_selected_result_identity"] = current_identity
    flow["selected_result_identity"] = current_identity
    flow["verified_detail_panel_identity"] = current_identity
    flow["identity_source"] = "current_selected_job"
    flow["current_selected_job_identity"] = current_identity
    if current_identity:
        flow["selected_detail_title"] = current_identity.get("title") or ""
        flow["selected_detail_company"] = current_identity.get("company") or ""
        flow["selected_detail_reference"] = current_identity.get("reference") or ""
    if not current_identity:
        flow["blocked_reason"] = "Could not read current selected JobServe job identity"
        flow["target_job_match_candidates"] = _jobserve_result_candidates(page)[:10]
        return {}
    if not str(current_identity.get("title") or "").strip():
        current_identity["title"] = "Currently visible JobServe job"
        current_identity["text"] = str(current_identity.get("text") or "LOCAL DEBUG ONLY: applying to currently visible JobServe job.")
        flow["local_debug_visible_job_warning"] = "LOCAL DEBUG ONLY: applying to currently visible JobServe job."
    target = _jobserve_target_from_current_identity(current_identity)
    flow["auto_selected_matched"] = True
    flow["first_job_selected"] = True
    return target


def _jobserve_visible_job_fallback_identity(page) -> dict[str, Any] | None:
    try:
        return page.evaluate(
            """() => {
                const text = (document.body.innerText || document.body.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 3000);
                const selected = document.querySelector('[aria-selected="true"], .selected, .active, [class*="selected" i], [class*="active" i]');
                const selectedText = selected ? (selected.innerText || selected.textContent || '').trim().replace(/\\s+/g, ' ') : '';
                const titleNode = selected?.querySelector?.('h1,h2,h3,.job-title,.title,[class*="title" i]') || document.querySelector('h1,h2,h3,.job-title,.title,[class*="title" i]');
                const companyNode = selected?.querySelector?.('.company,.recruiter,.employer,[class*="company" i],[class*="recruiter" i]') || document.querySelector('.company,.recruiter,.employer,[class*="company" i],[class*="recruiter" i]');
                const refMatch = text.match(/(?:ref(?:erence)?|job\\s*id)\\s*[:#]?\\s*([A-Z0-9-]{3,})/i) || location.href.match(/(?:jobid|job|shid)[=/:-]([A-Z0-9-]{3,})/i);
                return {
                    text: selectedText || text,
                    title: titleNode ? titleNode.innerText.trim() : '',
                    company: companyNode ? companyNode.innerText.trim() : '',
                    href: location.href,
                    reference: refMatch ? refMatch[1] : ''
                };
            }""",
        )
    except Exception:  # noqa: BLE001
        return None


def _jobserve_target_from_current_identity(identity: dict[str, Any]) -> dict[str, Any]:
    return {
        "job_id": None,
        "title": identity.get("title") or "",
        "original_title": identity.get("title") or "",
        "company_name": identity.get("company") or "",
        "original_company": identity.get("company") or "",
        "source_job_id": identity.get("reference") or "",
        "original_external_id": identity.get("reference") or "",
        "canonical_url": identity.get("href") or "",
        "location": identity.get("location") or "",
        "salary": identity.get("salary") or "",
        "identity_source": "current_selected_job",
    }


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
    haystack = _normalize_select_text(" ".join(str(candidate.get(key) or "") for key in ["text", "href", "title", "company", "company_name", "reference"]))
    score = 0
    for key, weight in [("source_job_id", 60), ("original_external_id", 60), ("canonical_url", 50), ("title", 30), ("original_title", 30), ("company_name", 20), ("original_company", 20)]:
        value = _normalize_select_text(str(target.get(key) or ""))
        if value and value in haystack:
            score += weight
    return score


def _jobserve_identity_matches(identity: dict[str, Any], target: dict[str, Any]) -> bool:
    return _jobserve_match_score(identity, target) >= 50 or (
        _jobserve_match_score(identity, target) >= 30
        and any(_normalize_select_text(str(target.get(key) or "")) in _normalize_select_text(str(identity.get("text") or "")) for key in ["company_name", "original_company"] if target.get(key))
    )


def _jobserve_submit_guard(flow: dict[str, Any], verified_identity: dict[str, Any], modal_identity: dict[str, Any], context, uploaded_cv: bool, unfilled_required: list[str]) -> str | None:
    if _jobserve_identity_clear_mismatch(modal_identity, verified_identity):
        return "JobServe application modal does not match intended job"
    if not _jobserve_identity_matches(verified_identity, flow.get("intended_job_identity") or {}):
        return "Verified JobServe detail panel does not match intended job"
    if not flow.get("email_filled") or not _jobserve_email_field_has_value(context):
        return "Email field is not filled"
    if not flow.get("confirmation_email_checked"):
        return "Confirmation email checkbox is not checked"
    if not flow.get("uk_status_selected"):
        return "Working status is not selected"
    if not uploaded_cv:
        return "CV is not attached"
    if unfilled_required:
        return f"Required fields missing: {', '.join(_dedupe(unfilled_required))}"
    return None


def _jobserve_email_field_has_value(context) -> bool:
    value = _jobserve_email_field_value(context)
    return bool(value and "@" in value)


def _jobserve_email_field_value(context) -> str | None:
    for pattern in [r"email address", r"email"]:
        try:
            value = context.get_by_label(re.compile(pattern, re.I)).first.input_value(timeout=500)
            if value:
                return str(value)
        except Exception:  # noqa: BLE001
            continue
    try:
        value = context.locator('input[type="email"], input[name*="email" i], input[id*="email" i]').first.input_value(timeout=500)
        return str(value) if value else None
    except Exception:  # noqa: BLE001
        return None


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
    email_label = _fill_jobserve_email_field(context, email)
    if email_label:
        filled.append("Email Address")
        flow["email_filled"] = True
        flow["email_value"] = email.value if email else None
        profile_diagnostics["mapped_fields"]["email"] = {"mapped": True, "label": email_label}
        _report_jobserve_step(step_callback, "jobserve_apply_email_filled", succeeded=True, label=email_label, email_value=email.value if email else None)
        _report_jobserve_step(step_callback, "email_filled", succeeded=True, label=email_label, email_value=email.value if email else None)
        debug.screenshot("application_email_filled")
    else:
        unfilled_required.append("Email Address")
        profile_diagnostics["mapped_fields"]["email"] = {"mapped": False, "reason": "email field missing or profile email missing"}
        _report_jobserve_step(step_callback, "jobserve_apply_email_filled", succeeded=False)
        _report_jobserve_step(step_callback, "email_filled", succeeded=False)
        debug.html("email_field_missing", context)

    _ensure_confirmation_email_checked(context, flow)
    _report_jobserve_step(step_callback, "jobserve_apply_confirmation_email_checked", succeeded=flow.get("confirmation_email_checked"))
    _report_jobserve_step(step_callback, "confirmation_checked", succeeded=flow.get("confirmation_email_checked"))
    debug.screenshot("application_confirmation_checkbox_checked")

    working_status = candidates.get("work_authorization")
    working_status_value = working_status.value if working_status else JOBSERVE_DEFAULTS["working_status"]
    if _select_work_status(context, working_status_value, select_diagnostics):
        filled.append("Working status in UK")
        flow["uk_status_selected"] = True
        flow["uk_status_value"] = working_status_value
        profile_diagnostics["mapped_fields"]["work_authorization"] = {"mapped": True, "label": "Working status in UK", "value": working_status_value}
        _report_jobserve_step(step_callback, "jobserve_apply_working_status_selected", succeeded=True, value=working_status_value)
        _report_jobserve_step(step_callback, "working_status_selected", succeeded=True, value=working_status_value)
        debug.screenshot("application_working_status_selected")
    else:
        unfilled_required.append("Working status in UK")
        profile_diagnostics["mapped_fields"]["work_authorization"] = {"mapped": False, "reason": "working status dropdown missing or configured value missing"}
        exceptions.append({"stage": "working_status", "type": "SelectOptionError", "message": "Working status dropdown missing or could not be selected", "traceback": None})
        _report_jobserve_step(step_callback, "jobserve_apply_working_status_selected", succeeded=False, value=working_status_value)
        _report_jobserve_step(step_callback, "working_status_selected", succeeded=False, value=working_status_value)
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
    upload_diagnostics["file_input_detected"] = bool(_jobserve_cv_file_input(context).count())
    cv_path = _cv_upload_path(profile, upload_diagnostics)
    debug.step("before_cv_upload", upload_diagnostics=upload_diagnostics)
    _report_jobserve_step(step_callback, "cv_upload_started", path=cv_path, file_input_detected=upload_diagnostics["file_input_detected"])
    uploaded_cv = False
    upload_started = time.perf_counter()
    if cv_path and upload_diagnostics.get("path_exists") and upload_diagnostics["file_input_detected"]:
        try:
            file_input = _jobserve_cv_file_input(context).first
            _retry_step("cv upload", lambda: file_input.set_input_files(cv_path, timeout=settings.playwright_step_timeout_ms))
            uploaded_cv = True
            filled.append("CV upload")
            upload_diagnostics["set_input_files_succeeded"] = True
            upload_diagnostics["displayed_file_name"] = _uploaded_cv_display_name(context, Path(cv_path).name)
            flow["cv_upload_succeeded"] = True
            _report_jobserve_step(step_callback, "jobserve_apply_cv_uploaded", succeeded=True, path=cv_path, file_name=Path(cv_path).name, displayed_file_name=upload_diagnostics["displayed_file_name"])
            _report_jobserve_step(step_callback, "cv_uploaded", succeeded=True, path=cv_path, file_name=Path(cv_path).name, displayed_file_name=upload_diagnostics["displayed_file_name"])
        except Exception as exc:  # noqa: BLE001
            payload = _exception_payload("cv_upload", exc, upload_diagnostics=dict(upload_diagnostics))
            exceptions.append(payload)
            upload_diagnostics["set_input_files_succeeded"] = False
            upload_diagnostics["set_input_files_error"] = payload
            unfilled_required.append("CV upload")
            warnings.append(f"Could not upload CV: {exc}")
            _report_jobserve_step(step_callback, "jobserve_apply_cv_uploaded", succeeded=False, path=cv_path, error=str(exc))
            _report_jobserve_step(step_callback, "cv_uploaded", succeeded=False, path=cv_path, error=str(exc))
            debug.html("cv_upload_failed", context)
    else:
        upload_diagnostics["failure_reason"] = "CV file input missing or worker-accessible CV path unavailable."
        exceptions.append({"stage": "cv_upload_preflight", "type": "FileNotFoundError", "message": upload_diagnostics["failure_reason"], "traceback": None, "upload_diagnostics": dict(upload_diagnostics)})
        unfilled_required.append("CV upload")
        _report_jobserve_step(step_callback, "jobserve_apply_cv_uploaded", succeeded=False, path=cv_path, error=upload_diagnostics["failure_reason"])
        _report_jobserve_step(step_callback, "cv_uploaded", succeeded=False, path=cv_path, error=upload_diagnostics["failure_reason"])
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
    diagnostic: dict[str, Any] = {}
    flow["confirmation_email_checked"] = _set_checkbox_by_label(page, [r"send confirmation.*email", r"confirmation.*email"], checked=True, diagnostic=diagnostic)
    if not flow["confirmation_email_checked"]:
        flow["confirmation_email_checked"] = _check_jobserve_generated_confirmation_checkbox(page, diagnostic)
    flow["confirmation_checkbox_diagnostic"] = diagnostic


def _check_jobserve_generated_confirmation_checkbox(page, diagnostic: dict[str, Any]) -> bool:
    for selector in [
        'input[type="checkbox"][name*="rptAppMand"][name*="ctl04"]',
        'input[type="checkbox"][name*="ctl04"]',
        'input[type="checkbox"][name*="rptAppMand"]',
    ]:
        try:
            locator = page.locator(selector).first
            if not locator.count():
                continue
            checked = locator.is_checked(timeout=500)
            if not checked:
                locator.check(timeout=1500)
            diagnostic.update({"checkbox_found": True, "strategy": "jobserve_generated_selector", "selector": selector, "initial_checked": checked, "final_checked": True, "result": "checked_after_selector"})
            return True
        except Exception as exc:  # noqa: BLE001
            diagnostic["generated_selector_error"] = str(exc)
            continue
    return False


def _uploaded_cv_display_name(page, file_name: str) -> str | None:
    try:
        text = page.locator("body").inner_text(timeout=1000)
    except Exception:  # noqa: BLE001
        return None
    return file_name if file_name in text else None


def _jobserve_cv_file_input(context):
    return context.locator(
        "#filCV, input#filCV, input[type=file][name*='CV' i], input[type=file][id*='CV' i], input[type=file]"
    )


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
    job_context: dict[str, Any] | None = None,
    direct_url: str | None = None,
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
    flow: dict[str, Any] = {"mode": "direct_job_url" if direct_url else "modal", "target": job_context or {}, "direct_url": direct_url, "identity_source": (job_context or {}).get("identity_source") or "db", "verified_detail_panel_identity": None, "blocked_reason": None}
    if direct_url:
        try:
            _report_stage(progress_callback, "jobserve_navigation", "start", url=direct_url)
            page.goto(direct_url, wait_until="domcontentloaded", timeout=settings.page_navigation_timeout_ms)
            page.wait_for_timeout(1200)
            _report_stage(progress_callback, "jobserve_navigation", "done", current_url=_safe_url(page), page_title=_safe_title(page))
        except Exception as exc:  # noqa: BLE001
            if mode == "submit_with_confirmation":
                _raise_jobserve_stage(debug, page, browser, "jobserve_navigation", "JobServe direct URL could not be opened.", exc=exc, extra={"jobserve_flow_diagnostics": dict(flow)})
            debug.final_error = "JobServe direct URL could not be opened."
            exceptions.append(_exception_payload("direct_job_url_open", exc, jobserve_flow_diagnostics=dict(flow)))
            return AssistApplyResult(status="review_required", filled_fields=[], unfilled_fields=[], unfilled_required_fields=[], uploaded_cv=False, submitted=False, warnings=[debug.final_error], screenshot_path=None, profile_diagnostics=profile_diagnostics, jobserve_flow_diagnostics=flow, timing_diagnostics={"total_runtime_ms": int((time.perf_counter() - flow_started) * 1000)}, upload_diagnostics=upload_diagnostics, select_diagnostics=select_diagnostics, exceptions=exceptions, **debug.result_kwargs(page))
    debug.step("initial_page_loaded")
    debug.screenshot("initial_page_loaded")
    if job_context and _jobserve_target_has_identity(job_context):
        detail_identity = _jobserve_detail_panel_identity(page)
        flow["verified_detail_panel_identity"] = detail_identity
        if detail_identity and not _jobserve_identity_matches(detail_identity, job_context):
            flow["blocked_reason"] = "Direct JobServe detail did not match intended job"
            debug.final_error = "Direct JobServe detail did not match intended job."
            debug.html("direct_detail_identity_mismatch", page)
            return AssistApplyResult(
                status="review_required",
                filled_fields=[],
                unfilled_fields=[],
                unfilled_required_fields=[],
                uploaded_cv=False,
                submitted=False,
                warnings=[debug.final_error],
                screenshot_path=debug.screenshot_paths[-1] if debug.screenshot_paths else None,
                profile_diagnostics=profile_diagnostics,
                jobserve_flow_diagnostics=flow,
                timing_diagnostics={"total_runtime_ms": int((time.perf_counter() - flow_started) * 1000)},
                upload_diagnostics=upload_diagnostics,
                select_diagnostics=select_diagnostics,
                exceptions=exceptions,
                **debug.result_kwargs(page),
            )
        flow["identity_check_result"] = "matched_direct_detail" if detail_identity else "direct_detail_identity_unavailable"

    _report_stage(progress_callback, "apply_button_lookup", "start", current_url=_safe_url(page), page_title=_safe_title(page))
    apply_target = _find_apply_target(page, browser)
    _report_stage(progress_callback, "apply_button_lookup", "done", apply_button_found=apply_target is not None, detected_buttons=_inventory_browser(page, browser)["buttons"][:20])
    debug.step("apply_button_lookup", apply_button_found=apply_target is not None, detected_buttons=_inventory_browser(page, browser)["buttons"][:20])
    if apply_target is None:
        debug.final_error = "No visible JobServe Apply button/link found."
        debug.html("no_apply_button_found")
        if mode == "submit_with_confirmation":
            _raise_jobserve_stage(debug, page, browser, "apply_button_lookup", debug.final_error, extra={"jobserve_flow_diagnostics": dict(flow)})
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
            jobserve_flow_diagnostics=flow,
            timing_diagnostics={"total_runtime_ms": int((time.perf_counter() - flow_started) * 1000)},
            upload_diagnostics=upload_diagnostics,
            select_diagnostics=select_diagnostics,
            exceptions=exceptions,
            **debug.result_kwargs(),
        )

    try:
        _report_stage(progress_callback, "apply_button_click", "start", current_url=_safe_url(page), page_title=_safe_title(page))
        pages_before = len(page.context.pages)
        apply_target.click(timeout=8000)
        page.wait_for_timeout(1000)
        target_page = _latest_page(browser) or page
        if len(page.context.pages) <= pages_before:
            target_page = page
        else:
            target_page.wait_for_load_state("domcontentloaded", timeout=15000)
        _report_stage(progress_callback, "apply_button_click", "done", current_url=_safe_url(target_page), page_title=_safe_title(target_page), popup_opened=target_page is not page)
    except Exception as exc:  # noqa: BLE001
        if mode == "submit_with_confirmation":
            _raise_jobserve_stage(debug, page, browser, "apply_button_click", "JobServe Apply button could not be clicked.", exc=exc, extra={"jobserve_flow_diagnostics": dict(flow)})
        raise
    debug.page = target_page
    debug.step("apply_button_clicked", apply_button_clicked=True, popup_opened=target_page is not page)

    _report_stage(progress_callback, "modal_wait", "start", current_url=_safe_url(target_page), page_title=_safe_title(target_page))
    target_page.wait_for_timeout(3500 if debug_mode else 1200)
    debug.screenshot("after_clicking_apply")
    context = _find_jobserve_form_context(target_page, browser)
    _report_stage(progress_callback, "modal_wait", "done", modal_found=context is not None, job_application_modal_found=context is not None, target_context=_context_name(context) if context else None)
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
        if mode == "submit_with_confirmation":
            _raise_jobserve_stage(debug, target_page, browser, "modal_wait", debug.final_error, target=target_page, extra={"jobserve_flow_diagnostics": dict(flow)})
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
            jobserve_flow_diagnostics=flow,
            timing_diagnostics={"total_runtime_ms": int((time.perf_counter() - flow_started) * 1000)},
            upload_diagnostics=upload_diagnostics,
            select_diagnostics=select_diagnostics,
            exceptions=exceptions,
            **debug.result_kwargs(target_page),
        )

    _report_stage(progress_callback, "form_detection", "start", current_url=_safe_url(target_page), page_title=_safe_title(target_page), target_context=_context_name(context))
    form_inventory = _inventory_context(context)
    _report_stage(progress_callback, "form_detection", "done", form_fields_detected=len(form_inventory["fields"]), cv_upload_input_detected=bool(form_inventory["file_inputs"]), required_dropdowns_detected=_detect_jobserve_dropdowns(form_inventory["selects"]), detected_fields=form_inventory["fields"], detected_selects=form_inventory["selects"])
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
        if mode == "submit_with_confirmation":
            _raise_jobserve_stage(debug, target_page, browser, "form_detection", debug.final_error, target=target_page, extra={"jobserve_flow_diagnostics": dict(flow), "detected_fields": form_inventory["fields"], "detected_selects": form_inventory["selects"]})
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
            jobserve_flow_diagnostics=flow,
            timing_diagnostics={"total_runtime_ms": int((time.perf_counter() - flow_started) * 1000)},
            upload_diagnostics=upload_diagnostics,
            select_diagnostics=select_diagnostics,
            exceptions=exceptions,
            **debug.result_kwargs(target_page),
        )

    if _captcha_visible(target_page):
        warnings.append("Captcha detected; manual review required.")

    _report_stage(progress_callback, "field_fill", "start", current_url=_safe_url(target_page), page_title=_safe_title(target_page))
    _report_jobserve_step(progress_callback, "account_toggle_disable_start", current_url=_safe_url(target_page), page_title=_safe_title(target_page))
    disabled_account_options = _disable_jobserve_account_options(context, warnings)
    flow["account_toggles_turned_off_initial"] = disabled_account_options
    _report_jobserve_step(progress_callback, "account_toggle_disable_done", succeeded=True, disabled=disabled_account_options)
    required = ["email"]
    for key, patterns in {
        "email": [r"email address", r"email"],
        "first_name": [r"first name"],
        "last_name": [r"last name", r"surname"],
        "phone": [r"phone", r"mobile"],
    }.items():
        label = _fill_jobserve_email_field(context, candidates.get(key)) if key == "email" else _fill_by_label_patterns(context, patterns, candidates.get(key))
        if label:
            filled.append(label)
            profile_diagnostics.setdefault("mapped_fields", {})[key] = {"mapped": True, "label": label}
            if key == "email":
                _report_jobserve_step(progress_callback, "email_filled", succeeded=True, label=label)
        elif key in required:
            unfilled_required.append(key.replace("_", " "))
            profile_diagnostics.setdefault("mapped_fields", {})[key] = {"mapped": False, "reason": "field not found or candidate missing"}
            _report_jobserve_step(progress_callback, "email_filled", succeeded=False)

    _ensure_confirmation_email_checked(context, flow)
    _report_jobserve_step(progress_callback, "confirmation_checked", succeeded=flow.get("confirmation_email_checked"))
    if candidates.get("work_authorization"):
        if _select_work_status(context, candidates["work_authorization"].value, select_diagnostics):
            filled.append("Working status in UK")
            flow["uk_status_selected"] = True
            flow["uk_status_value"] = candidates["work_authorization"].value
            profile_diagnostics.setdefault("mapped_fields", {})["work_authorization"] = {"mapped": True, "label": "Working status in UK"}
            _report_jobserve_step(progress_callback, "working_status_selected", succeeded=True, value=candidates["work_authorization"].value)
        else:
            unfilled.append("Working status in UK")
            profile_diagnostics.setdefault("mapped_fields", {})["work_authorization"] = {"mapped": False, "reason": "select/fill failed"}
            _report_jobserve_step(progress_callback, "working_status_selected", succeeded=False, value=candidates["work_authorization"].value)
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
    _report_stage(progress_callback, "field_fill", "done", filled_fields=_dedupe(filled), unfilled_fields=_dedupe(unfilled), unfilled_required_fields=_dedupe(unfilled_required))

    upload_diagnostics["detected_file_inputs"] = form_inventory["file_inputs"]
    upload_diagnostics["file_input_detected"] = bool(context.locator("input[type=file]").count())
    cv_path = _cv_upload_path(profile, upload_diagnostics)
    debug.step("before_cv_upload", upload_diagnostics=upload_diagnostics)
    _report_stage(progress_callback, "cv_upload", "start", path=cv_path, file_input_detected=upload_diagnostics["file_input_detected"])
    _report_jobserve_step(progress_callback, "cv_upload_started", path=cv_path, file_input_detected=upload_diagnostics["file_input_detected"])
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
            flow["cv_upload_succeeded"] = True
            _report_jobserve_step(progress_callback, "cv_uploaded", succeeded=True, path=cv_path, file_name=Path(cv_path).name)
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
            _report_jobserve_step(progress_callback, "cv_uploaded", succeeded=False, path=cv_path, error=str(exc))
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
        _report_jobserve_step(progress_callback, "cv_uploaded", succeeded=False, path=cv_path, error=upload_diagnostics["failure_reason"])
    debug.screenshot("after_cv_upload_attempt")
    _report_stage(progress_callback, "cv_upload", "done", uploaded_cv=uploaded_cv, upload_diagnostics=upload_diagnostics)
    if mode == "submit_with_confirmation" and not uploaded_cv:
        _raise_jobserve_stage(
            debug,
            target_page,
            browser,
            "cv_upload",
            debug.final_error or upload_diagnostics.get("failure_reason") or "CV upload failed.",
            target=target_page,
            extra={"jobserve_flow_diagnostics": dict(flow), "upload_diagnostics": dict(upload_diagnostics), "exceptions": list(exceptions)},
        )

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
        debug.screenshot("before_final_submit")
        debug.html("before_final_submit", context)
        _report_stage(progress_callback, "pre_submit_verification", "start", unfilled_required_fields=_dedupe(unfilled_required), uploaded_cv=uploaded_cv)
        if unfilled_required:
            _raise_jobserve_stage(
                debug,
                target_page,
                browser,
                "pre_submit_verification",
                f"Required fields missing: {', '.join(_dedupe(unfilled_required))}",
                target=target_page,
                extra={"jobserve_flow_diagnostics": dict(flow), "unfilled_required_fields": _dedupe(unfilled_required), "upload_diagnostics": dict(upload_diagnostics), "select_diagnostics": list(select_diagnostics)},
            )
        apply_button = _jobserve_apply_button(context)
        if apply_button.count() == 0:
            debug.final_error = "Submit button not found in JobServe apply form."
            debug.html("submit_button_not_found", context)
            _raise_jobserve_stage(debug, target_page, browser, "final_submit_click", debug.final_error, target=target_page, extra={"jobserve_flow_diagnostics": dict(flow)})
        _report_stage(progress_callback, "pre_submit_verification", "done", unfilled_required_fields=[], uploaded_cv=uploaded_cv)
        try:
            _report_stage(progress_callback, "final_submit_click", "start", current_url=_safe_url(target_page), page_title=_safe_title(target_page))
            apply_button.click(timeout=8000)
            _report_stage(progress_callback, "final_submit_click", "done", current_url=_safe_url(target_page), page_title=_safe_title(target_page))
        except Exception as exc:  # noqa: BLE001
            _raise_jobserve_stage(debug, target_page, browser, "final_submit_click", "Final JobServe Apply button could not be clicked.", target=target_page, exc=exc, extra={"jobserve_flow_diagnostics": dict(flow)})
        flow["final_apply_clicked"] = True
        _report_jobserve_step(progress_callback, "final_apply_clicked", succeeded=True)
        try:
            _report_stage(progress_callback, "success_confirmation", "start", current_url=_safe_url(target_page), page_title=_safe_title(target_page))
            confirmation_text = _wait_for_jobserve_submission_success(target_page, browser)
            _report_stage(progress_callback, "success_confirmation", "done", current_url=_safe_url(target_page), page_title=_safe_title(target_page), confirmation_text=confirmation_text)
        except Exception as exc:  # noqa: BLE001
            _raise_jobserve_stage(debug, target_page, browser, "success_confirmation", "JobServe submission success confirmation was not detected.", target=target_page, exc=exc, extra={"jobserve_flow_diagnostics": dict(flow)})
        debug.screenshot("after_final_submit")
        debug.html("after_final_submit", target_page)
        flow["confirmation_text"] = confirmation_text
        flow["submitted_confirmation_detected"] = True
        if re.search(r"already applied|already submitted|you have applied", confirmation_text, flags=re.I):
            flow["already_applied_detected"] = True
        submitted = True
        status = "submitted"
        _report_jobserve_step(progress_callback, "submitted_message_seen", succeeded=True, confirmation_text=confirmation_text)
        flow["account_toggles_turned_off"] = _disable_jobserve_account_options(context, warnings)
        flow["registration_toggle_disabled"] = any("register a Job Seeker account" in item for item in flow["account_toggles_turned_off"])
        _report_jobserve_step(progress_callback, "account_toggle_disabled", succeeded=flow["registration_toggle_disabled"], disabled=flow["account_toggles_turned_off"])
        flow["modal_closed"] = _close_modal(target_page)
        _report_jobserve_step(progress_callback, "modal_closed", succeeded=flow["modal_closed"])
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
        jobserve_flow_diagnostics=flow,
        timing_diagnostics={"total_runtime_ms": int((time.perf_counter() - flow_started) * 1000)},
        progress={"current_step": "submitted" if submitted else "review_required", "elapsed_ms": int((time.perf_counter() - flow_started) * 1000), "last_heartbeat_at": utcnow().isoformat()},
        upload_diagnostics=upload_diagnostics,
        select_diagnostics=select_diagnostics,
        exceptions=exceptions,
        confirmation_text=str(flow.get("confirmation_text") or "") or None,
        **debug.result_kwargs(target_page),
    )


def _fill_by_label_patterns(page, patterns: list[str], candidate: FieldCandidate | None) -> str | None:
    if candidate is None:
        return None
    for pattern in patterns:
        try:
            locator = page.get_by_label(re.compile(pattern, re.I)).first
            locator.fill(candidate.value, timeout=1500)
            return pattern
        except Exception:  # noqa: BLE001
            continue
    return None


def _fill_jobserve_email_field(page, candidate: FieldCandidate | None) -> str | None:
    label = _fill_by_label_patterns(page, [r"email address", r"email"], candidate)
    if label:
        return label
    if candidate is None:
        return None
    for selector in [
        'input[type="email"]',
        'input[name*="email" i]',
        'input[id*="email" i]',
        'input#Q0006_ans',
        'input[name*="Q0006_ans"]',
        'input[type="text"][name*="rptAppMand"][name*="Q0006_ans"]',
        'input[type="text"][id^="Q"][name*="rptAppMand"]',
    ]:
        try:
            locator = page.locator(selector).first
            if not locator.count():
                continue
            locator.fill(candidate.value, timeout=1500)
            return selector
        except Exception:  # noqa: BLE001
            continue
    return None


def _select_work_status(page, value: str, select_diagnostics: list[dict[str, Any]] | None = None) -> bool:
    target = "UK Citizen" if _normalize_select_text(value) in {"uk", "uk citizen", "citizen"} else value
    if jobserve_click_dropdown_option(
        page,
        {"labels": [r"working status", r"work status", r"status in uk", r"eligible.*uk"], "selectors": ['select[name*="status" i]', 'select[id*="status" i]', 'select[name*="work" i]', 'select[id*="work" i]']},
        target,
        field_name="Working status in UK",
        diagnostics=select_diagnostics,
    ):
        return True
    for pattern in [r"working status", r"work status", r"status in uk", r"eligible.*uk"]:
        locator = page.get_by_label(re.compile(pattern, re.I)).first
        try:
            locator.fill(target, timeout=1500)
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
    phrases = [
        "I would like to register a Job Seeker account",
        "register a Job Seeker account",
        "make my CV searchable",
        "CV searchable",
        "job alerts",
        "create an account",
    ]
    for context in _page_and_frame_contexts(page):
        if not hasattr(context, "evaluate"):
            for text in phrases:
                diagnostic: dict[str, Any] = {}
                if _set_checkbox_by_label(context, [re.escape(text)], checked=False, diagnostic=diagnostic, click_unknown=True):
                    if diagnostic.get("clicked") or diagnostic.get("result") in {"unchecked_after_click", "unknown_clicked_once"}:
                        warnings.append(f"Disabled option: {text}.")
                        disabled.append(text)
            continue
        try:
            clicked = context.evaluate(
                """(phrases) => {
                    const norm = (value) => String(value || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                    const phrasesNorm = phrases.map(norm);
                    const visible = (el) => {
                        const style = window.getComputedStyle(el);
                        const rect = el.getBoundingClientRect();
                        return style && style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
                    };
                    const textFor = (el) => [
                        el.innerText,
                        el.textContent,
                        el.getAttribute('aria-label'),
                        el.getAttribute('title'),
                        el.getAttribute('name'),
                        el.getAttribute('id')
                    ].filter(Boolean).join(' ');
                    const labelText = (input) => {
                        const id = input.getAttribute('id');
                        const label = id ? document.querySelector(`label[for="${CSS.escape(id)}"]`) : null;
                        const parentLabel = input.closest('label');
                        return [label && label.innerText, parentLabel && parentLabel.innerText, textFor(input)].filter(Boolean).join(' ');
                    };
                    const controls = Array.from(document.querySelectorAll('input[type="checkbox"], [role="checkbox"]'));
                    const disabled = [];
                    for (const control of controls) {
                        if (!visible(control)) continue;
                        const haystack = norm(labelText(control));
                        const phrase = phrasesNorm.find((candidate) => candidate && haystack.includes(candidate));
                        if (!phrase) continue;
                        const rawPhrase = phrases[phrasesNorm.indexOf(phrase)];
                        const checked = control.matches('input[type="checkbox"]')
                            ? Boolean(control.checked)
                            : String(control.getAttribute('aria-checked') || '').toLowerCase() === 'true';
                        if (checked || !control.matches('input[type="checkbox"]')) {
                            control.click();
                            disabled.push(rawPhrase);
                        }
                    }
                    return disabled;
                }""",
                phrases,
                timeout=1500,
            )
            for text in clicked or []:
                warnings.append(f"Disabled option: {text}.")
                disabled.append(str(text))
        except Exception as exc:  # noqa: BLE001
            logger.info("jobserve_account_options_dom_disable_failed context=%s error=%s", _context_name(context), exc)
    return disabled


def _page_and_frame_contexts(page) -> list[Any]:
    contexts = [page]
    try:
        contexts.extend(frame for frame in page.frames if frame is not page.main_frame)
    except Exception:  # noqa: BLE001
        pass
    return contexts


def _jobserve_apply_button(page):
    buttons = page.get_by_role("button", name=re.compile(r"^apply$", re.I)).all()
    if buttons:
        return buttons[-1]
    return page.locator("input[type=submit], button[type=submit]").last


def _wait_for_jobserve_submission_success(page, browser) -> str:
    success_patterns = [
        re.compile(r"your application has been submitted", re.I),
        re.compile(r"application (?:has been )?received", re.I),
        re.compile(r"already applied", re.I),
        re.compile(r"already submitted", re.I),
        re.compile(r"you have applied", re.I),
    ]
    deadline = time.time() + (settings.page_navigation_timeout_ms / 1000)
    last_exc: Exception | None = None
    while time.time() < deadline:
        for context in _all_contexts(page, browser):
            for pattern in success_patterns:
                try:
                    locator = context.get_by_text(pattern).first
                    locator.wait_for(timeout=1000)
                    try:
                        return locator.inner_text(timeout=500)
                    except Exception:  # noqa: BLE001
                        return pattern.pattern
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    continue
        page.wait_for_timeout(500)
    raise RuntimeError("JobServe submission confirmation not detected.") from last_exc


def _jobserve_confirmation_means_submitted(value: str | None) -> bool:
    return bool(
        value
        and re.search(
            r"your application has been submitted|application (?:has been )?received|already applied|already submitted|you have applied",
            value,
            flags=re.I,
        )
    )


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
