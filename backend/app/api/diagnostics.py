from __future__ import annotations

import secrets

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, status
from pydantic import BaseModel

from app.config import settings
from app.diagnostics.assist_apply_runs import new_run_id, read_run_status, run_assist_apply_probe_background, write_run_status
from app.services.queue import enqueue_or_background

router = APIRouter(prefix="/diagnostics", tags=["diagnostics"])


class AssistApplyDiagnosticRequest(BaseModel):
    user_id: int = 1
    safe_mode: bool = True
    submit_allowed: bool = False


def _require_diagnostic_token(authorization: str | None) -> None:
    expected = settings.diagnostic_admin_token
    if not expected:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Diagnostic admin token is not configured")
    prefix = "Bearer "
    if not authorization or not authorization.startswith(prefix):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing diagnostic authorization")
    supplied = authorization[len(prefix) :]
    if not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid diagnostic authorization")


@router.post("/assist-apply/{application_id}")
def start_assist_apply_diagnostic(
    application_id: int,
    payload: AssistApplyDiagnosticRequest,
    background_tasks: BackgroundTasks,
    authorization: str | None = Header(default=None),
):
    _require_diagnostic_token(authorization)
    run_id = new_run_id()
    write_run_status(
        run_id,
        status="queued",
        application_id=application_id,
        user_id=payload.user_id,
        safe_mode=payload.safe_mode,
        submit_allowed=payload.submit_allowed,
        latest_progress={"phase": "queued"},
    )
    try:
        rq_job_id = enqueue_or_background(
            background_tasks,
            run_assist_apply_probe_background,
            run_id,
            application_id,
            payload.user_id,
            payload.safe_mode,
            payload.submit_allowed,
            job_id=f"assist-diagnostic-{run_id}",
            job_timeout=settings.apply_timeout_seconds,
            result_ttl=settings.rq_result_ttl_seconds,
            failure_ttl=settings.rq_failure_ttl_seconds,
        )
    except Exception as exc:  # noqa: BLE001
        report = {
            "overall_status": "failed",
            "failed_phase": "remote_diagnostic_enqueue_failed",
            "exact_error": str(exc),
            "recommended_fix": "Verify Render Redis connectivity and worker queue configuration.",
        }
        write_run_status(
            run_id,
            status="failed",
            application_id=application_id,
            user_id=payload.user_id,
            safe_mode=payload.safe_mode,
            submit_allowed=payload.submit_allowed,
            latest_progress={"phase": "remote_diagnostic_enqueue_failed"},
            final_report=report,
            markdown_summary=(
                "# Assist Apply Probe\n\n"
                "- overall_status: failed\n"
                "- failed_phase: remote_diagnostic_enqueue_failed\n"
                f"- exact_error: {exc}\n"
                "- recommended_fix: Verify Render Redis connectivity and worker queue configuration.\n"
            ),
        )
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail={"run_id": run_id, **report}) from exc
    state = read_run_status(run_id) or {}
    state["rq_job_id"] = rq_job_id
    write_run_status(
        run_id,
        status="queued",
        application_id=application_id,
        user_id=payload.user_id,
        safe_mode=payload.safe_mode,
        submit_allowed=payload.submit_allowed,
        latest_progress={"phase": "queued", "rq_job_id": rq_job_id},
        final_report=state.get("final_report"),
        markdown_summary=state.get("markdown_summary"),
        artifact_links=state.get("artifact_links") or [],
    )
    return {"run_id": run_id, "status": "queued", "latest_progress": {"phase": "queued", "rq_job_id": rq_job_id}}


@router.get("/assist-apply/runs/{run_id}")
def get_assist_apply_diagnostic_run(run_id: str, authorization: str | None = Header(default=None)):
    _require_diagnostic_token(authorization)
    state = read_run_status(run_id)
    if state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Diagnostic run not found")
    return {
        "run_id": run_id,
        "status": state.get("status"),
        "latest_progress": state.get("latest_progress") or {},
        "final_report": state.get("final_report"),
        "markdown_summary": state.get("markdown_summary"),
        "artifact_links": state.get("artifact_links") or [],
    }
