from datetime import datetime, timedelta, timezone
import logging
from time import perf_counter

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.models import Job, JobRescoreRun, JobRescoreRunFailure, MissingSkill, User
from app.db.session import SessionLocal
from app.schemas.database import JobRescoreRunStart, JobRescoreRunStatus
from app.services.job_scoring import preload_scoring_context, score_job_against_profile
from app.services.profile import get_profile

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = {"queued", "running"}
COMMIT_EVERY_JOBS = 10
STALE_HEARTBEAT_SECONDS = 180
PER_JOB_TIMEOUT_SECONDS = 30
WHOLE_RUN_TIMEOUT_SECONDS = 900


def start_rescore_run(db: Session, user: User) -> tuple[JobRescoreRunStart, bool]:
    mark_stale_rescore_runs(db)
    active = db.scalar(select(JobRescoreRun).where(JobRescoreRun.status.in_(ACTIVE_STATUSES)).order_by(JobRescoreRun.id.desc()))
    if active is not None:
        logger.info("job_rescore_run_active_reused run_id=%s status=%s user_id=%s", active.id, active.status, user.id)
        return JobRescoreRunStart(run_id=active.id, status=active.status), False

    total = db.scalar(select(func.count(Job.id)).where(Job.status != "excluded")) or 0
    skipped = db.scalar(select(func.count(Job.id)).where(Job.status == "excluded")) or 0
    now = _utcnow()
    run = JobRescoreRun(
        status="queued",
        total=total,
        total_jobs=total,
        skipped=skipped,
        scored=0,
        completed_jobs=0,
        failed=0,
        failed_jobs=0,
        last_heartbeat_at=now,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    logger.info("job_rescore_run_created run_id=%s user_id=%s total_jobs=%s skipped=%s status=%s", run.id, user.id, total, skipped, run.status)
    return JobRescoreRunStart(run_id=run.id, status=run.status), True


def retry_rescore_run(db: Session, run_id: int, user: User) -> tuple[JobRescoreRunStart | None, bool]:
    existing = db.get(JobRescoreRun, run_id)
    if existing is None:
        return None, False
    if existing.status in ACTIVE_STATUSES:
        return JobRescoreRunStart(run_id=existing.id, status=existing.status), False
    return start_rescore_run(db, user)


def cancel_rescore_run(db: Session, run_id: int) -> JobRescoreRunStart | None:
    run = db.get(JobRescoreRun, run_id)
    if run is None:
        return None
    if run.status in ACTIVE_STATUSES:
        run.status = "canceled"
        run.error = "Canceled by user"
        run.finished_at = _utcnow()
        _heartbeat(run)
        db.commit()
        logger.info("job_rescore_run_canceled run_id=%s completed_jobs=%s failed_jobs=%s total_jobs=%s", run.id, run.completed_jobs, run.failed_jobs, run.total_jobs)
    return JobRescoreRunStart(run_id=run.id, status=run.status)


def get_rescore_run_status(db: Session, run_id: int) -> JobRescoreRunStatus | None:
    mark_stale_rescore_runs(db)
    run = db.get(JobRescoreRun, run_id)
    if run is None:
        return None
    return _status(run)


def mark_stale_rescore_runs(db: Session, *, heartbeat_seconds: int = STALE_HEARTBEAT_SECONDS) -> int:
    cutoff = _utcnow() - timedelta(seconds=heartbeat_seconds)
    candidates = db.scalars(select(JobRescoreRun).where(JobRescoreRun.status.in_(ACTIVE_STATUSES))).all()
    marked = 0
    for run in candidates:
        heartbeat = _as_aware(run.last_heartbeat_at or run.started_at)
        if heartbeat < cutoff:
            run.status = "stalled"
            run.error = f"Rescore run stalled after no heartbeat since {heartbeat.isoformat()}"
            run.finished_at = _utcnow()
            marked += 1
            logger.warning("job_rescore_run_stalled run_id=%s completed_jobs=%s failed_jobs=%s total_jobs=%s", run.id, run.completed_jobs, run.failed_jobs, run.total_jobs)
    if marked:
        db.commit()
    return marked


def cleanup_stalled_rescore_runs(db: Session) -> int:
    return mark_stale_rescore_runs(db)


def run_rescore_background(run_id: int, user_id: int) -> None:
    started = perf_counter()
    logger.info("job_rescore_background_start run_id=%s user_id=%s", run_id, user_id)
    with SessionLocal() as db:
        run = db.get(JobRescoreRun, run_id)
        user = db.get(User, user_id)
        if run is None or user is None:
            logger.error("job_rescore_background_missing_context run_id=%s user_id=%s run_found=%s user_found=%s", run_id, user_id, run is not None, user is not None)
            return
        try:
            _set_running(db, run)
            profile = get_profile(db, user)
            if profile is None:
                raise ValueError("No saved profile found. Save CV/profile before rescoring jobs.")
            jobs = db.scalars(select(Job).where(Job.status != "excluded").order_by(Job.id)).all()
            job_ids = [job.id for job in jobs]
            context = preload_scoring_context(db, jobs, user, profile)
            run.total = len(jobs)
            run.total_jobs = len(jobs)
            _heartbeat(run)
            db.execute(delete(JobRescoreRunFailure).where(JobRescoreRunFailure.run_id == run_id))
            if job_ids:
                db.execute(
                    delete(MissingSkill)
                    .where(MissingSkill.user_id == user.id, MissingSkill.job_id.in_(job_ids))
                    .execution_options(synchronize_session=False)
                )
            db.commit()
            logger.info("job_rescore_jobs_loaded run_id=%s total_jobs=%s", run_id, len(jobs))

            slowest_job_id: int | None = None
            slowest_ms = 0
            cumulative_job_ms = 0
            for index, job in enumerate(jobs, start=1):
                if index == 1 or index % COMMIT_EVERY_JOBS == 0:
                    db.refresh(run)
                if run.status == "canceled":
                    logger.info("job_rescore_run_cancel_observed run_id=%s job_id=%s completed_jobs=%s", run_id, job.id, run.completed_jobs)
                    return
                if perf_counter() - started > WHOLE_RUN_TIMEOUT_SECONDS:
                    run = db.get(JobRescoreRun, run_id)
                    if run is not None:
                        run.status = "stalled"
                        run.error = f"Whole-run timeout after {WHOLE_RUN_TIMEOUT_SECONDS}s"
                        run.finished_at = _utcnow()
                        _heartbeat(run)
                        db.commit()
                    logger.error("job_rescore_run_timeout run_id=%s completed_jobs=%s failed_jobs=%s total_jobs=%s", run_id, run.completed_jobs if run else None, run.failed_jobs if run else None, run.total_jobs if run else None)
                    return

                job_started = perf_counter()
                logger.info("job_rescore_job_start run_id=%s job_id=%s", run_id, job.id)
                try:
                    with db.begin_nested():
                        score_job_against_profile(db, job, user, profile, context, refresh_missing=False)
                    elapsed = perf_counter() - job_started
                    if elapsed > PER_JOB_TIMEOUT_SECONDS:
                        raise TimeoutError(f"Job scoring exceeded {PER_JOB_TIMEOUT_SECONDS}s")
                    elapsed_ms = int(elapsed * 1000)
                    cumulative_job_ms += elapsed_ms
                    if elapsed_ms > slowest_ms:
                        slowest_ms = elapsed_ms
                        slowest_job_id = job.id
                    run.completed_jobs += 1
                    run.scored = run.completed_jobs
                    _heartbeat(run)
                    if (run.completed_jobs + run.failed_jobs) % COMMIT_EVERY_JOBS == 0 or run.completed_jobs + run.failed_jobs == run.total_jobs:
                        db.commit()
                    else:
                        db.flush()
                    logger.info("job_rescore_job_completed run_id=%s job_id=%s duration_ms=%s", run_id, job.id, elapsed_ms)
                except Exception as exc:  # noqa: BLE001
                    run = db.get(JobRescoreRun, run_id)
                    if run is None:
                        logger.error("job_rescore_run_missing_after_failure run_id=%s job_id=%s", run_id, job.id)
                        return
                    run.failed_jobs += 1
                    run.failed = run.failed_jobs
                    run.error = str(exc)
                    _heartbeat(run)
                    db.add(JobRescoreRunFailure(run_id=run_id, job_id=job.id, error=str(exc)))
                    db.commit()
                    logger.exception("job_rescore_job_failed run_id=%s job_id=%s error=%s", run_id, job.id, exc)

            run = db.get(JobRescoreRun, run_id)
            if run is None:
                return
            run.status = "failed" if run.failed_jobs and run.completed_jobs == 0 else "completed"
            run.finished_at = _utcnow()
            _heartbeat(run)
            db.commit()
            avg_ms = int(cumulative_job_ms / run.completed_jobs) if run.completed_jobs else 0
            logger.info(
                "job_rescore_run_completed run_id=%s status=%s total_jobs=%s completed_jobs=%s failed_jobs=%s skipped=%s duration_ms=%s avg_ms_per_job=%s slowest_job_id=%s slowest_job_ms=%s",
                run.id,
                run.status,
                run.total_jobs,
                run.completed_jobs,
                run.failed_jobs,
                run.skipped,
                int((perf_counter() - started) * 1000),
                avg_ms,
                slowest_job_id,
                slowest_ms,
            )
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            run = db.get(JobRescoreRun, run_id)
            if run is not None:
                run.status = "failed"
                run.error = str(exc)
                run.finished_at = _utcnow()
                _heartbeat(run)
                db.commit()
            logger.exception("job_rescore_run_failed run_id=%s error=%s duration_ms=%s", run_id, exc, int((perf_counter() - started) * 1000))


def _set_running(db: Session, run: JobRescoreRun) -> None:
    run.status = "running"
    _heartbeat(run)
    db.commit()
    logger.info("job_rescore_run_running run_id=%s", run.id)


def _heartbeat(run: JobRescoreRun) -> None:
    run.last_heartbeat_at = _utcnow()


def _status(run: JobRescoreRun) -> JobRescoreRunStatus:
    completed = run.completed_jobs or run.scored
    failed = run.failed_jobs or run.failed
    total = run.total_jobs or run.total
    return JobRescoreRunStatus(
        run_id=run.id,
        status=run.status,
        total=total,
        scored=completed,
        skipped=run.skipped,
        failed=failed,
        total_jobs=total,
        completed_jobs=completed,
        failed_jobs=failed,
        estimated_seconds_remaining=_estimated_seconds_remaining(run, total, completed, failed),
        started_at=run.started_at,
        finished_at=run.finished_at,
        last_heartbeat_at=run.last_heartbeat_at,
        error=run.error,
    )


def _estimated_seconds_remaining(run: JobRescoreRun, total: int, completed: int, failed: int) -> float | None:
    if run.status not in ACTIVE_STATUSES:
        return 0.0
    processed = completed + failed
    remaining = max(0, total - processed)
    if processed <= 0 or remaining <= 0:
        return None
    elapsed = (_utcnow() - _as_aware(run.started_at)).total_seconds()
    if elapsed <= 0:
        return None
    return round((elapsed / processed) * remaining, 1)


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _as_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value
