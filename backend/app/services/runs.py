from datetime import datetime, timedelta, timezone

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.db.models import ApplicationPrepareRun, JobApplyStrategyRun, JobAvailabilityRun, JobRescoreRun, ScrapeRun, User
from app.schemas.database import UnifiedRun
from app.services.applications import start_prepare_applications_run
from app.services.apply_strategy import start_apply_strategy_run
from app.services.job_availability import start_availability_run
from app.services.rescore_runs import cancel_rescore_run, retry_rescore_run
from app.services.run_tracking import finish_run

STALE_THRESHOLD_SECONDS = 180
ACTIVE_STATUSES = {"queued", "running", "pending", "started"}


def list_unified_runs(db: Session, *, run_type: str = "all", status: str = "all", limit: int = 50) -> list[UnifiedRun]:
    rows: list[UnifiedRun] = []
    if run_type in {"all", "scrape"}:
        rows.extend(_scrape_runs(db, limit))
    if run_type in {"all", "rescore"}:
        rows.extend(_rescore_runs(db, limit))
    if run_type in {"all", "availability"}:
        rows.extend(_availability_runs(db, limit))
    if run_type in {"all", "application_prepare"}:
        rows.extend(_application_prepare_runs(db, limit))
    if run_type in {"all", "apply_strategy"}:
        rows.extend(_apply_strategy_runs(db, limit))
    if status != "all":
        rows = [row for row in rows if row.status == status]
    return sorted(rows, key=lambda row: row.started_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)[:limit]


def retry_run(db: Session, run_type: str, run_id: int, user: User) -> tuple[UnifiedRun | None, bool]:
    if run_type == "rescore":
        started, created = retry_rescore_run(db, run_id, user)
        return (_run_from_rescore(db.get(JobRescoreRun, started.run_id)) if started else None), created
    if run_type == "availability":
        started, created = start_availability_run(db)
        return _run_from_availability(db.get(JobAvailabilityRun, started.run_id)), created
    if run_type == "application_prepare":
        started, created = start_prepare_applications_run(db, user)
        return _run_from_application_prepare(db.get(ApplicationPrepareRun, started.run_id)), created
    if run_type == "apply_strategy":
        started, created = start_apply_strategy_run(db)
        return _run_from_apply_strategy(db.get(JobApplyStrategyRun, started.run_id)), created
    if run_type == "scrape":
        return None, False
    return None, False


def cancel_run(db: Session, run_type: str, run_id: int) -> UnifiedRun | None:
    if run_type == "rescore":
        result = cancel_rescore_run(db, run_id)
        return _run_from_rescore(db.get(JobRescoreRun, result.run_id)) if result else None
    model = _model_for_type(run_type)
    if model is None:
        return None
    run = db.get(model, run_id)
    if run is None:
        return None
    if _status(run.status, getattr(run, "last_heartbeat_at", None), run.started_at, run.finished_at) in ACTIVE_STATUSES | {"stalled"}:
        if run_type == "scrape":
            run.status = "canceled"
            run.error_message = "Canceled by user"
            run.finished_at = datetime.now(tz=timezone.utc)
            db.commit()
            return _run_from_scrape(run)
        finish_run(run, "canceled", "Canceled by user")
        db.commit()
    return _to_unified(run_type, run)


def _scrape_runs(db: Session, limit: int) -> list[UnifiedRun]:
    return [_run_from_scrape(run) for run in db.scalars(select(ScrapeRun).order_by(desc(ScrapeRun.id)).limit(limit)).all()]


def _rescore_runs(db: Session, limit: int) -> list[UnifiedRun]:
    return [_run_from_rescore(run) for run in db.scalars(select(JobRescoreRun).order_by(desc(JobRescoreRun.id)).limit(limit)).all()]


def _availability_runs(db: Session, limit: int) -> list[UnifiedRun]:
    return [_run_from_availability(run) for run in db.scalars(select(JobAvailabilityRun).order_by(desc(JobAvailabilityRun.id)).limit(limit)).all()]


def _application_prepare_runs(db: Session, limit: int) -> list[UnifiedRun]:
    return [_run_from_application_prepare(run) for run in db.scalars(select(ApplicationPrepareRun).order_by(desc(ApplicationPrepareRun.id)).limit(limit)).all()]


def _apply_strategy_runs(db: Session, limit: int) -> list[UnifiedRun]:
    return [_run_from_apply_strategy(run) for run in db.scalars(select(JobApplyStrategyRun).order_by(desc(JobApplyStrategyRun.id)).limit(limit)).all()]


def _run_from_scrape(run: ScrapeRun | None) -> UnifiedRun | None:
    if run is None:
        return None
    total = run.jobs_found or 0
    succeeded = (run.jobs_created or 0) + (run.jobs_updated or 0)
    return UnifiedRun(
        id=str(run.id),
        type="scrape",
        status=_status(run.status, None, run.started_at, run.finished_at),
        total=total,
        processed=max(total, succeeded + (run.jobs_skipped or 0)),
        succeeded=succeeded,
        failed=len(run.errors or []) if run.errors else (1 if run.error_message else 0),
        skipped=run.jobs_skipped or 0,
        error=run.error_message,
        started_at=run.started_at,
        last_heartbeat_at=None,
        finished_at=run.finished_at,
        duration_seconds=_duration(run.started_at, run.finished_at),
    )


def _run_from_rescore(run: JobRescoreRun | None) -> UnifiedRun | None:
    if run is None:
        return None
    completed = run.completed_jobs or run.scored
    failed = run.failed_jobs or run.failed
    total = run.total_jobs or run.total
    return UnifiedRun(
        id=str(run.id),
        type="rescore",
        status=_status(run.status, run.last_heartbeat_at, run.started_at, run.finished_at),
        total=total,
        processed=completed + failed,
        succeeded=completed,
        failed=failed,
        skipped=run.skipped,
        error=run.error,
        started_at=run.started_at,
        last_heartbeat_at=run.last_heartbeat_at,
        finished_at=run.finished_at,
        duration_seconds=_duration(run.started_at, run.finished_at),
    )


def _run_from_availability(run: JobAvailabilityRun | None) -> UnifiedRun | None:
    if run is None:
        return None
    return UnifiedRun(
        id=str(run.id),
        type="availability",
        status=_status(run.status, run.last_heartbeat_at, run.started_at, run.finished_at),
        total=run.total,
        processed=run.processed,
        succeeded=run.checked,
        failed=run.failed,
        skipped=max(0, run.total - run.processed),
        error=run.error,
        started_at=run.started_at,
        last_heartbeat_at=run.last_heartbeat_at,
        finished_at=run.finished_at,
        duration_seconds=_duration(run.started_at, run.finished_at),
    )


def _run_from_application_prepare(run: ApplicationPrepareRun | None) -> UnifiedRun | None:
    if run is None:
        return None
    return UnifiedRun(
        id=str(run.id),
        type="application_prepare",
        status=_status(run.status, run.last_heartbeat_at, run.started_at, run.finished_at),
        total=run.total,
        processed=run.processed,
        succeeded=run.queued,
        failed=run.failed,
        skipped=run.skipped,
        error=run.error,
        started_at=run.started_at,
        last_heartbeat_at=run.last_heartbeat_at,
        finished_at=run.finished_at,
        duration_seconds=_duration(run.started_at, run.finished_at),
    )


def _run_from_apply_strategy(run: JobApplyStrategyRun | None) -> UnifiedRun | None:
    if run is None:
        return None
    return UnifiedRun(
        id=str(run.id),
        type="apply_strategy",
        status=_status(run.status, run.last_heartbeat_at, run.started_at, run.finished_at),
        total=run.total,
        processed=run.processed,
        succeeded=run.classified,
        failed=run.failed,
        skipped=max(0, run.total - run.processed),
        error=run.error,
        started_at=run.started_at,
        last_heartbeat_at=run.last_heartbeat_at,
        finished_at=run.finished_at,
        duration_seconds=_duration(run.started_at, run.finished_at),
    )


def _to_unified(run_type: str, run) -> UnifiedRun | None:
    if run_type == "scrape":
        return _run_from_scrape(run)
    if run_type == "rescore":
        return _run_from_rescore(run)
    if run_type == "availability":
        return _run_from_availability(run)
    if run_type == "application_prepare":
        return _run_from_application_prepare(run)
    if run_type == "apply_strategy":
        return _run_from_apply_strategy(run)
    return None


def _model_for_type(run_type: str):
    return {
        "scrape": ScrapeRun,
        "availability": JobAvailabilityRun,
        "application_prepare": ApplicationPrepareRun,
        "apply_strategy": JobApplyStrategyRun,
    }.get(run_type)


def _status(status: str, heartbeat: datetime | None, started: datetime | None, finished: datetime | None) -> str:
    if status in {"completed", "failed", "stalled", "canceled"}:
        return status
    if finished is not None:
        return status
    marker = heartbeat or started
    if status in ACTIVE_STATUSES and marker is not None and _aware(marker) < datetime.now(tz=timezone.utc) - timedelta(seconds=STALE_THRESHOLD_SECONDS):
        return "stalled"
    return status


def _duration(started: datetime | None, finished: datetime | None) -> float | None:
    if started is None:
        return None
    end = finished or datetime.now(tz=timezone.utc)
    return round((_aware(end) - _aware(started)).total_seconds(), 1)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value
