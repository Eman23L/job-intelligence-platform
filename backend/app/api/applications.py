import logging
from pathlib import Path
import time

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import Job, User
from app.db.session import get_db
from app.diagnostics.assist_apply_runs import new_run_id, read_run_status, run_assist_apply_probe_background, write_run_status
from app.schemas.database import ApplicationPrepareRunStart, ApplicationPrepareRunStatus, ApplicationsList, AssistApplyDiagnosticRun, AssistApplyRequest, AssistApplyResult, AutonomousRealSubmitRunResult, AutonomousRealSubmitStatus
from app.services.apply_agent import BrowserAutomationError, assist_apply_application, mark_queued_assist, update_queued_assist_metadata, run_assist_apply_background
from app.services.applications import (
    get_prepare_applications_run_status,
    list_applications,
    minimum_apply_score,
    run_prepare_applications_background,
    start_prepare_applications_run,
)
from app.services.autonomous_submit import autonomous_submit_status, run_autonomous_real_submit_canary, run_autonomous_real_submit_canary_background
from app.services.queue import enqueue_or_background, queue_enabled, redis_url_host

router = APIRouter(prefix="/applications", tags=["applications"])
logger = logging.getLogger(__name__)
DEBUG_ARTIFACT_ROOT = Path("backend/runtime/apply_debug").resolve()
_APPLICATIONS_CACHE: ApplicationsList | None = None


def _default_user(db: Session) -> User:
    user = db.scalar(select(User).order_by(User.id))
    if user is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No seeded user found")
    return user


@router.get("", response_model=ApplicationsList)
def get_applications(db: Session = Depends(get_db)):
    global _APPLICATIONS_CACHE
    started = time.perf_counter()
    path = "/applications"
    user = _default_user(db)
    try:
        threshold_started = time.perf_counter()
        threshold = minimum_apply_score(db, user)
        threshold_duration_ms = int((time.perf_counter() - threshold_started) * 1000)
        list_started = time.perf_counter()
        items = list_applications(db, user)
        list_duration_ms = int((time.perf_counter() - list_started) * 1000)
        response = ApplicationsList(items=items, minimum_apply_score=threshold)
        _APPLICATIONS_CACHE = response
        logger.info(
            "applications_request_done path=%s count=%s threshold_duration_ms=%s list_duration_ms=%s total_duration_ms=%s",
            path,
            len(items),
            threshold_duration_ms,
            list_duration_ms,
            int((time.perf_counter() - started) * 1000),
        )
        return response
    except Exception as exc:  # noqa: BLE001
        total_duration_ms = int((time.perf_counter() - started) * 1000)
        logger.exception("applications_request_failed path=%s total_duration_ms=%s error=%s", path, total_duration_ms, exc)
        if _APPLICATIONS_CACHE is not None:
            return ApplicationsList(
                items=_APPLICATIONS_CACHE.items,
                minimum_apply_score=_APPLICATIONS_CACHE.minimum_apply_score,
                warning="Applications data is temporarily stale because the latest refresh failed.",
            )
        raise


@router.post("/prepare", response_model=ApplicationPrepareRunStart)
def prepare_application_queue(background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    user = _default_user(db)
    started, created = start_prepare_applications_run(db, user)
    if created:
        enqueue_or_background(background_tasks, run_prepare_applications_background, started.run_id, user.id, job_id=f"application-prepare-{started.run_id}")
    return started


@router.get("/prepare-runs/{run_id}", response_model=ApplicationPrepareRunStatus)
def get_prepare_application_run(run_id: int, db: Session = Depends(get_db)):
    result = get_prepare_applications_run_status(db, run_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prepare run not found")
    return result


@router.get("/debug-artifacts/{artifact_path:path}")
def get_apply_debug_artifact(artifact_path: str):
    target = (DEBUG_ARTIFACT_ROOT / artifact_path).resolve()
    try:
        target.relative_to(DEBUG_ARTIFACT_ROOT)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Debug artifact not found") from exc
    if not target.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Debug artifact not found")
    return FileResponse(target)


@router.post("/{application_id}/assist-apply", response_model=AssistApplyResult)
def assist_apply(application_id: int, background_tasks: BackgroundTasks, payload: AssistApplyRequest | None = None, db: Session = Depends(get_db)):
    job = db.get(Job, application_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found")
    user = _default_user(db)
    mode = payload.mode if payload else "review_only"
    debug_mode = bool(payload.debug_mode) if payload else False
    queue_is_enabled = queue_enabled()
    if queue_is_enabled:
        logger.info(
            "assist_apply_enqueue_start service_type=%s application_id=%s user_id=%s mode=%s debug_mode=%s queue_enabled=%s queue_name=%s redis_host=%s enqueue_success=false",
            settings.service_type,
            application_id,
            user.id,
            mode,
            debug_mode,
            queue_is_enabled,
            settings.queue_name,
            redis_url_host(),
        )
        mark_queued_assist(db, job, mode=mode, debug_mode=debug_mode)
        try:
            rq_job_id = enqueue_or_background(
                background_tasks,
                run_assist_apply_background,
                application_id,
                user.id,
                mode,
                debug_mode,
                job_timeout=settings.apply_timeout_seconds,
                result_ttl=settings.rq_result_ttl_seconds,
                failure_ttl=settings.rq_failure_ttl_seconds,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "assist_apply_enqueue_failed service_type=%s application_id=%s user_id=%s mode=%s debug_mode=%s queue_enabled=%s queue_name=%s redis_host=%s enqueue_success=false",
                settings.service_type,
                application_id,
                user.id,
                mode,
                debug_mode,
                queue_is_enabled,
                settings.queue_name,
                redis_url_host(),
            )
            result = job.assisted_result or {}
            warnings = [*result.get("warnings", []), f"assist_apply_enqueue_failed: {exc}"]
            job.assisted_result = {
                **result,
                "status": "failed",
                "final_error": "assist_apply_enqueue_failed",
                "warnings": warnings,
                "progress": {
                    **(result.get("progress") if isinstance(result.get("progress"), dict) else {}),
                    "current_step": "assist_apply_enqueue_failed",
                    "message": str(exc),
                },
                "jobserve_flow_diagnostics": {
                    **(result.get("jobserve_flow_diagnostics") if isinstance(result.get("jobserve_flow_diagnostics"), dict) else {}),
                    "queue_diagnostics": {
                        "application_id": application_id,
                        "mode": mode,
                        "queue_enabled": queue_is_enabled,
                        "queue_name": settings.queue_name,
                        "redis_host": redis_url_host(),
                        "enqueue_success": False,
                        "error": str(exc),
                    },
                },
            }
            job.assisted_warnings = warnings
            db.commit()
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail={"error": "assist_apply_enqueue_failed", "message": str(exc)}) from exc
        result = update_queued_assist_metadata(db, job, rq_job_id=rq_job_id, queue_name=settings.queue_name, redis_host=redis_url_host())
        logger.info(
            "assist_apply_queued service_type=%s application_id=%s user_id=%s mode=%s debug_mode=%s queue_enabled=%s queue_name=%s redis_host=%s rq_job_id=%s enqueue_success=true",
            settings.service_type,
            application_id,
            user.id,
            mode,
            debug_mode,
            queue_is_enabled,
            settings.queue_name,
            redis_url_host(),
            rq_job_id,
        )
        return result

    logger.info("assist_apply_running_inline service_type=%s application_id=%s mode=%s debug_mode=%s", settings.service_type, application_id, mode, debug_mode)
    try:
        return assist_apply_application(db, job, user, mode=mode, debug_mode=debug_mode)
    except BrowserAutomationError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail={"error": exc.error, "message": exc.message}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/{application_id}/assist-apply/diagnostics", response_model=AssistApplyDiagnosticRun)
def start_failed_submit_diagnostic(application_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    job = db.get(Job, application_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found")
    user = _default_user(db)
    run_id = new_run_id()
    write_run_status(
        run_id,
        status="queued",
        application_id=application_id,
        user_id=user.id,
        safe_mode=True,
        submit_allowed=False,
        latest_progress={"phase": "queued", "trigger": "submit_failure"},
    )
    rq_job_id = enqueue_or_background(
        background_tasks,
        run_assist_apply_probe_background,
        run_id,
        application_id,
        user.id,
        True,
        False,
        job_id=f"assist-diagnostic-{run_id}",
        job_timeout=settings.apply_timeout_seconds,
        result_ttl=settings.rq_result_ttl_seconds,
        failure_ttl=settings.rq_failure_ttl_seconds,
    )
    state = write_run_status(
        run_id,
        status="queued",
        application_id=application_id,
        user_id=user.id,
        safe_mode=True,
        submit_allowed=False,
        latest_progress={"phase": "queued", "trigger": "submit_failure", "rq_job_id": rq_job_id},
    )
    return _diagnostic_response(run_id, state)


@router.get("/{application_id}/assist-apply/diagnostics/{run_id}", response_model=AssistApplyDiagnosticRun)
def get_failed_submit_diagnostic(application_id: int, run_id: str, db: Session = Depends(get_db)):
    if db.get(Job, application_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found")
    state = read_run_status(run_id)
    if state is None or state.get("application_id") != application_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Diagnostic run not found")
    return _diagnostic_response(run_id, state)


def _diagnostic_response(run_id: str, state: dict) -> AssistApplyDiagnosticRun:
    return AssistApplyDiagnosticRun(
        run_id=run_id,
        status=str(state.get("status") or "queued"),
        latest_progress=state.get("latest_progress") or {},
        final_report=state.get("final_report"),
        markdown_summary=state.get("markdown_summary"),
        artifact_links=state.get("artifact_links") or [],
    )


@router.get("/autonomous-real-submit", response_model=AutonomousRealSubmitStatus)
def get_autonomous_real_submit_status(db: Session = Depends(get_db)):
    user = _default_user(db)
    return AutonomousRealSubmitStatus(**autonomous_submit_status(db, user))


@router.post("/autonomous-real-submit", response_model=AutonomousRealSubmitRunResult)
def run_autonomous_real_submit(background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    user = _default_user(db)
    if queue_enabled():
        rq_job_id = enqueue_or_background(
            background_tasks,
            run_autonomous_real_submit_canary_background,
            user.id,
            job_id=f"autonomous-real-submit-{user.id}-{int(time.time() * 1000)}",
            job_timeout=settings.apply_timeout_seconds,
            result_ttl=settings.rq_result_ttl_seconds,
            failure_ttl=settings.rq_failure_ttl_seconds,
        )
        result = autonomous_submit_status(db, user).get("last_result") or {}
        return AutonomousRealSubmitRunResult(
            status="queued",
            application_id=result.get("application_id"),
            submitted=False,
            failed_phase=None,
            exact_error=None,
            recommended_fix="Autonomous canary queued. Background run still processing.",
            diagnostic_run_id=None,
            orchestration_steps=result.get("orchestration_steps") if isinstance(result.get("orchestration_steps"), list) else [],
            created_at=result.get("created_at") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
    return AutonomousRealSubmitRunResult(**run_autonomous_real_submit_canary(db, user))
