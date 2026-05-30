from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import traceback
from typing import Any
from uuid import uuid4

from app.diagnostics.assist_apply_probe import build_report, failure_report, markdown_report, write_report_files

RUN_ROOT = Path("backend/runtime/assist_apply_diagnostics/runs")
RUN_TTL_SECONDS = 86400


def new_run_id() -> str:
    return uuid4().hex


def run_dir(run_id: str) -> Path:
    return RUN_ROOT / run_id


def status_path(run_id: str) -> Path:
    return run_dir(run_id) / "status.json"


def markdown_path(run_id: str) -> Path:
    return run_dir(run_id) / "latest_assist_apply_probe.md"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redis_key(run_id: str) -> str:
    return f"assist_apply_diagnostic_run:{run_id}"


def _read_redis_status(run_id: str) -> dict[str, Any] | None:
    try:
        from app.config import settings
        from app.services.queue import redis_connection

        if not settings.queue_enabled:
            return None
        value = redis_connection().get(_redis_key(run_id))
    except Exception:  # noqa: BLE001
        return None
    if not value:
        return None
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    return json.loads(value)


def _write_redis_status(run_id: str, payload: dict[str, Any]) -> None:
    try:
        from app.config import settings
        from app.services.queue import redis_connection

        if not settings.queue_enabled:
            return
        redis_connection().setex(_redis_key(run_id), RUN_TTL_SECONDS, json.dumps(payload, sort_keys=True))
    except Exception:  # noqa: BLE001
        return


def write_run_status(
    run_id: str,
    *,
    status: str,
    application_id: int,
    user_id: int,
    safe_mode: bool,
    submit_allowed: bool,
    latest_progress: dict[str, Any] | None = None,
    final_report: dict[str, Any] | None = None,
    markdown_summary: str | None = None,
    artifact_links: list[str] | None = None,
) -> dict[str, Any]:
    root = run_dir(run_id)
    root.mkdir(parents=True, exist_ok=True)
    existing = read_run_status(run_id) or {}
    payload = {
        **existing,
        "run_id": run_id,
        "status": status,
        "application_id": application_id,
        "user_id": user_id,
        "safe_mode": safe_mode,
        "submit_allowed": submit_allowed,
        "latest_progress": latest_progress or existing.get("latest_progress") or {},
        "final_report": final_report if final_report is not None else existing.get("final_report"),
        "markdown_summary": markdown_summary if markdown_summary is not None else existing.get("markdown_summary"),
        "artifact_links": artifact_links if artifact_links is not None else existing.get("artifact_links") or [],
        "updated_at": _now(),
        "created_at": existing.get("created_at") or _now(),
    }
    status_path(run_id).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    _write_redis_status(run_id, payload)
    return payload


def read_run_status(run_id: str) -> dict[str, Any] | None:
    redis_state = _read_redis_status(run_id)
    if redis_state is not None:
        return redis_state
    path = status_path(run_id)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def run_assist_apply_probe_background(
    run_id: str,
    application_id: int,
    user_id: int = 1,
    safe_mode: bool = True,
    submit_allowed: bool = False,
) -> None:
    write_run_status(
        run_id,
        status="running",
        application_id=application_id,
        user_id=user_id,
        safe_mode=safe_mode,
        submit_allowed=submit_allowed,
        latest_progress={"phase": "probe_started"},
    )
    try:
        report = build_report(
            application_id,
            user_id,
            safe_mode=safe_mode,
            submit_allowed=submit_allowed,
            output_dir=run_dir(run_id),
            run_browser_navigation=True,
        )
    except Exception as exc:  # noqa: BLE001
        report = write_report_files(
            failure_report(
                application_id,
                user_id,
                safe_mode=safe_mode,
                submit_allowed=submit_allowed,
                failed_phase="remote_diagnostic_probe_error",
                exact_error=str(exc),
                traceback_text=traceback.format_exc(),
            ),
            run_dir(run_id),
        )
    final_status = "passed" if report.get("overall_status") == "ok" else "failed"
    markdown = markdown_path(run_id).read_text(encoding="utf-8") if markdown_path(run_id).is_file() else markdown_report(report)
    write_run_status(
        run_id,
        status=final_status,
        application_id=application_id,
        user_id=user_id,
        safe_mode=safe_mode,
        submit_allowed=submit_allowed,
        latest_progress={"phase": report.get("failed_phase") or "complete", "overall_status": report.get("overall_status")},
        final_report=report,
        markdown_summary=markdown,
        artifact_links=[str(path) for path in report.get("artifact_paths") or []],
    )
    _persist_latest_safe_diagnostic(
        run_id,
        application_id,
        status=final_status,
        safe_mode=safe_mode,
        submit_allowed=submit_allowed,
        report=report,
        artifact_links=[str(path) for path in report.get("artifact_paths") or []],
    )


def _persist_latest_safe_diagnostic(
    run_id: str,
    application_id: int,
    *,
    status: str,
    safe_mode: bool,
    submit_allowed: bool,
    report: dict[str, Any],
    artifact_links: list[str],
) -> None:
    if not safe_mode or submit_allowed:
        return
    try:
        from sqlalchemy.orm.attributes import flag_modified

        from app.db.models import Job
        from app.db.session import SessionLocal

        with SessionLocal() as db:
            job = db.get(Job, application_id)
            if job is None:
                return
            assisted = job.assisted_result if isinstance(job.assisted_result, dict) else {}
            job.assisted_result = {
                **assisted,
                "latest_safe_diagnostic": {
                    "run_id": run_id,
                    "status": status,
                    "safe_mode": safe_mode,
                    "submit_allowed": submit_allowed,
                    "overall_status": report.get("overall_status"),
                    "failed_phase": report.get("failed_phase"),
                    "exact_error": report.get("exact_error"),
                    "recommended_fix": report.get("recommended_fix"),
                    "source_job_id": (((report.get("phases") or {}).get("jobserve_url_resolution") or {}).get("data") or {}).get("diagnostics", {}).get("source_job_id"),
                    "job_title": (((report.get("phases") or {}).get("db_lookup") or {}).get("data") or {}).get("job_title"),
                    "job_company": (((report.get("phases") or {}).get("db_lookup") or {}).get("data") or {}).get("job_company"),
                    "cv_found": (((report.get("phases") or {}).get("db_lookup") or {}).get("data") or {}).get("cv_found"),
                    "artifact_links": artifact_links,
                    "completed_at": _now(),
                },
            }
            flag_modified(job, "assisted_result")
            db.commit()
    except Exception:  # noqa: BLE001
        return
