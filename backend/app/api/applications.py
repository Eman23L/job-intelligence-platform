import logging
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import Job, User
from app.db.session import get_db
from app.schemas.database import ApplicationPrepareRunStart, ApplicationPrepareRunStatus, ApplicationsList, AssistApplyRequest, AssistApplyResult
from app.services.apply_agent import BrowserAutomationError, assist_apply_application, mark_queued_assist, update_queued_assist_metadata, run_assist_apply_background
from app.services.applications import (
    get_prepare_applications_run_status,
    list_applications,
    minimum_apply_score,
    run_prepare_applications_background,
    start_prepare_applications_run,
)
from app.services.queue import enqueue_or_background, queue_enabled, redis_url_host

router = APIRouter(prefix="/applications", tags=["applications"])
logger = logging.getLogger(__name__)
DEBUG_ARTIFACT_ROOT = Path("backend/runtime/apply_debug").resolve()


def _default_user(db: Session) -> User:
    user = db.scalar(select(User).order_by(User.id))
    if user is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No seeded user found")
    return user


@router.get("", response_model=ApplicationsList)
def get_applications(db: Session = Depends(get_db)):
    user = _default_user(db)
    return ApplicationsList(items=list_applications(db, user), minimum_apply_score=minimum_apply_score(db, user))


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
