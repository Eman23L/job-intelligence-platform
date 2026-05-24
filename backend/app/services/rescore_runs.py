from datetime import datetime, timezone
import logging
from time import perf_counter

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Job, JobRescoreRun, User
from app.db.session import SessionLocal
from app.schemas.database import JobRescoreRunStart, JobRescoreRunStatus
from app.services.job_scoring import score_job_against_profile
from app.services.profile import get_profile

logger = logging.getLogger(__name__)


def start_rescore_run(db: Session, user: User) -> tuple[JobRescoreRunStart, bool]:
    active = db.scalar(select(JobRescoreRun).where(JobRescoreRun.status == "running").order_by(JobRescoreRun.id.desc()))
    if active is not None:
        return JobRescoreRunStart(run_id=active.id, status=active.status), False
    total = db.scalar(select(func.count(Job.id)).where(Job.status != "excluded")) or 0
    skipped = db.scalar(select(func.count(Job.id)).where(Job.status == "excluded")) or 0
    run = JobRescoreRun(status="running", total=total, skipped=skipped, scored=0, failed=0)
    db.add(run)
    db.commit()
    db.refresh(run)
    logger.info("job rescore queued run_id=%s user_id=%s total=%s skipped=%s", run.id, user.id, total, skipped)
    return JobRescoreRunStart(run_id=run.id, status=run.status), True


def get_rescore_run_status(db: Session, run_id: int) -> JobRescoreRunStatus | None:
    run = db.get(JobRescoreRun, run_id)
    if run is None:
        return None
    return JobRescoreRunStatus(
        run_id=run.id,
        status=run.status,
        total=run.total,
        scored=run.scored,
        skipped=run.skipped,
        failed=run.failed,
        error=run.error,
    )


def run_rescore_background(run_id: int, user_id: int) -> None:
    started = perf_counter()
    with SessionLocal() as db:
        run = db.get(JobRescoreRun, run_id)
        user = db.get(User, user_id)
        if run is None or user is None:
            return
        try:
            profile = get_profile(db, user)
            if profile is None:
                raise ValueError("No saved profile found. Save CV/profile before rescoring jobs.")
            jobs = db.scalars(select(Job).where(Job.status != "excluded").order_by(Job.id)).all()
            run.total = len(jobs)
            db.commit()
            for job in jobs:
                try:
                    score_job_against_profile(db, job, user, profile)
                    run.scored += 1
                except Exception as exc:  # noqa: BLE001
                    db.rollback()
                    run = db.get(JobRescoreRun, run_id)
                    if run is None:
                        return
                    run.failed += 1
                    run.error = str(exc)
                    logger.warning("job rescore item failed run_id=%s job_id=%s error=%s", run_id, job.id, exc)
                db.commit()
            run.status = "failed" if run.failed and run.scored == 0 else "completed"
            run.finished_at = datetime.now(tz=timezone.utc)
            db.commit()
            logger.info(
                "job rescore finished run_id=%s status=%s total=%s scored=%s skipped=%s failed=%s duration_ms=%s",
                run.id,
                run.status,
                run.total,
                run.scored,
                run.skipped,
                run.failed,
                int((perf_counter() - started) * 1000),
            )
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            run = db.get(JobRescoreRun, run_id)
            if run is not None:
                run.status = "failed"
                run.error = str(exc)
                run.finished_at = datetime.now(tz=timezone.utc)
                db.commit()
            logger.exception("job rescore failed run_id=%s duration_ms=%s", run_id, int((perf_counter() - started) * 1000))
