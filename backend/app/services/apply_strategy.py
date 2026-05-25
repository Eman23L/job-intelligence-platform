from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import logging
import re
from urllib.parse import urlparse

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.db.models import Job, JobApplyStrategyRun, JobScore
from app.db.session import SessionLocal
from app.schemas.database import JobApplyStrategyResult, JobApplyStrategyRunStart, JobApplyStrategyRunStatus
from app.services.run_tracking import finish_run, heartbeat, utcnow

logger = logging.getLogger(__name__)

ACTIVE_RUN_STATUSES = {"running", "queued"}
EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", re.IGNORECASE)


@dataclass(frozen=True)
class StrategyClassification:
    strategy: str
    difficulty: str
    reason: str


def classify_apply_strategy(db: Session, job: Job) -> JobApplyStrategyResult:
    classification = classify_job(job)
    job.apply_strategy = classification.strategy
    job.apply_difficulty = classification.difficulty
    job.apply_strategy_reason = classification.reason
    score = _latest_score(db, job.id)
    readiness = calculate_apply_readiness(job, score)
    if score is not None:
        score.apply_readiness_score = readiness
    db.commit()
    db.refresh(job)
    return JobApplyStrategyResult(
        job_id=job.id,
        apply_strategy=job.apply_strategy,
        apply_difficulty=job.apply_difficulty,
        apply_strategy_reason=job.apply_strategy_reason,
        apply_readiness_score=readiness,
    )


def classify_job(job: Job) -> StrategyClassification:
    url = (job.canonical_url or "").strip()
    text = " ".join(part for part in [job.title, job.company_name, job.description_text] if part).lower()
    source_name = (job.source.name if job.source else "").lower()
    source_type = (job.source.source_type if job.source else "").lower()

    if not url:
        return StrategyClassification("blocked", "blocked", "Missing apply URL.")
    if url.lower().startswith("mailto:") or EMAIL_PATTERN.search(url) or EMAIL_PATTERN.search(text):
        return StrategyClassification("recruiter_email", "easy", "Recruiter email or mailto apply route detected.")

    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    combined = f"{host} {path} {source_name} {source_type} {text}"

    if "jobserve.com" in combined:
        if "apply" in path or "job" in path:
            return StrategyClassification("jobserve_apply_easy", "easy", "JobServe hosted apply modal flow detected.")
        return StrategyClassification("jobserve_apply", "medium", "JobServe application flow detected.")
    if "greenhouse.io" in host or "boards.greenhouse.io" in host:
        return StrategyClassification("greenhouse", "medium", "Greenhouse application flow detected.")
    if "lever.co" in host:
        return StrategyClassification("lever", "medium", "Lever application flow detected.")
    if "myworkdayjobs.com" in host or "workday" in combined:
        return StrategyClassification("workday", "hard", "Workday application detected - likely manual/harder apply.")
    if "taleo.net" in host or "taleo" in combined:
        return StrategyClassification("taleo", "hard", "Taleo application flow detected - likely manual/harder apply.")
    if "successfactors" in combined:
        return StrategyClassification("successfactors", "hard", "SuccessFactors application flow detected - likely manual/harder apply.")
    if parsed.scheme in {"http", "https"} and host:
        return StrategyClassification("unknown", "unknown", "External application site could not be classified.")
    return StrategyClassification("blocked", "blocked", "Apply URL is not usable.")


def calculate_apply_readiness(job: Job, score: JobScore | None) -> Decimal:
    fit = float(score.total_score) if score is not None and score.total_score is not None else 0.0
    readiness = fit * 0.65
    availability = job.availability_status or "unknown"
    difficulty = job.apply_difficulty or "unknown"

    readiness += {"active": 18.0, "unknown": 8.0}.get(availability, -25.0)
    readiness += {"easy": 14.0, "medium": 8.0, "hard": 1.0, "unknown": 0.0, "blocked": -35.0}.get(difficulty, 0.0)
    readiness += 8.0 if job.canonical_url else -20.0
    if job.application_status in {"applied", "skipped"}:
        readiness -= 40.0
    return Decimal(str(round(max(0.0, min(100.0, readiness)), 2)))


def refresh_apply_readiness(job: Job, score: JobScore | None) -> Decimal | None:
    if score is None:
        return None
    score.apply_readiness_score = calculate_apply_readiness(job, score)
    return score.apply_readiness_score


def start_apply_strategy_run(db: Session, job_ids: list[int] | None = None) -> tuple[JobApplyStrategyRunStart, bool]:
    active = db.scalar(select(JobApplyStrategyRun).where(JobApplyStrategyRun.status.in_(ACTIVE_RUN_STATUSES)).order_by(JobApplyStrategyRun.id.desc()))
    if active is not None:
        return JobApplyStrategyRunStart(run_id=active.id, status=active.status), False
    total_query = select(func.count(Job.id)).where(Job.status != "excluded")
    if job_ids:
        total_query = total_query.where(Job.id.in_(job_ids))
    run = JobApplyStrategyRun(status="queued", total=db.scalar(total_query) or 0, last_heartbeat_at=utcnow())
    db.add(run)
    db.commit()
    db.refresh(run)
    logger.info("apply_strategy_run_created run_id=%s total=%s", run.id, run.total)
    return JobApplyStrategyRunStart(run_id=run.id, status=run.status), True


def get_apply_strategy_run_status(db: Session, run_id: int) -> JobApplyStrategyRunStatus | None:
    run = db.get(JobApplyStrategyRun, run_id)
    if run is None:
        return None
    return JobApplyStrategyRunStatus(
        run_id=run.id,
        status=run.status,
        total=run.total,
        processed=run.processed,
        classified=run.classified,
        failed=run.failed,
        error=run.error,
        started_at=run.started_at,
        finished_at=run.finished_at,
        last_heartbeat_at=run.last_heartbeat_at,
    )


def run_apply_strategy_background(run_id: int, job_ids: list[int] | None = None) -> None:
    logger.info("apply_strategy_background_start run_id=%s", run_id)
    with SessionLocal() as db:
        run = db.get(JobApplyStrategyRun, run_id)
        if run is None:
            logger.error("apply_strategy_run_missing run_id=%s", run_id)
            return
        try:
            run.status = "running"
            heartbeat(run)
            db.commit()
            query = select(Job).options(selectinload(Job.source)).where(Job.status != "excluded").order_by(Job.id)
            if job_ids:
                query = query.where(Job.id.in_(job_ids))
            jobs = db.scalars(query).all()
            scores = {score.job_id: score for score in db.scalars(select(JobScore).where(JobScore.job_id.in_([job.id for job in jobs]))).all()}
            run.total = len(jobs)
            heartbeat(run)
            db.commit()
            for job in jobs:
                try:
                    classification = classify_job(job)
                    job.apply_strategy = classification.strategy
                    job.apply_difficulty = classification.difficulty
                    job.apply_strategy_reason = classification.reason
                    refresh_apply_readiness(job, scores.get(job.id))
                    run.classified += 1
                    logger.info("apply_strategy_job_classified run_id=%s job_id=%s strategy=%s difficulty=%s", run_id, job.id, job.apply_strategy, job.apply_difficulty)
                except Exception as exc:  # noqa: BLE001
                    run.failed += 1
                    run.error = str(exc)
                    logger.exception("apply_strategy_job_failed run_id=%s job_id=%s error=%s", run_id, job.id, exc)
                finally:
                    run.processed += 1
                    heartbeat(run)
                    db.commit()
            finish_run(run, "failed" if run.failed and run.classified == 0 else "completed", run.error if run.failed and run.classified == 0 else None)
            db.commit()
            logger.info("apply_strategy_run_completed run_id=%s total=%s classified=%s failed=%s", run.id, run.total, run.classified, run.failed)
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            run = db.get(JobApplyStrategyRun, run_id)
            if run is not None:
                finish_run(run, "failed", str(exc))
                db.commit()
            logger.exception("apply_strategy_run_failed run_id=%s error=%s", run_id, exc)


def _latest_score(db: Session, job_id: int) -> JobScore | None:
    return db.scalar(select(JobScore).where(JobScore.job_id == job_id).order_by(JobScore.scored_at.desc()))
