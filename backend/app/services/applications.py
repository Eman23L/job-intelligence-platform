from decimal import Decimal
from datetime import datetime, timedelta, timezone
import logging
import time

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.orm import Session

from app.db.models import ApplicationPrepareRun, Job, JobScore, User
from app.db.session import SessionLocal
from app.schemas.database import ApplicationItem, ApplicationPrepareRunStart, ApplicationPrepareRunStatus
from app.services.apply_strategy import classify_job, refresh_apply_readiness
from app.services.job_availability import QUEUEABLE_AVAILABILITY_STATUSES, check_job_availability
from app.services.profile import get_profile
from app.services.run_tracking import finish_run, heartbeat, utcnow

rq_job_failure = None  # Kept only so tests can assert the applications list does not call external RQ checks.

APPLICATION_STATUSES = {"not_started", "ready_to_apply", "opened", "applied", "skipped", "failed"}
QUEUEABLE_RECOMMENDATIONS = {"apply", "maybe"}
TERMINAL_APPLICATION_STATUSES = {"applied", "skipped"}
ACTIVE_RUN_STATUSES = {"running", "queued"}
ASSIST_QUEUE_TIMEOUT = timedelta(minutes=2)
ASSIST_WORKER_STARTED_HANDOFF_TIMEOUT = timedelta(seconds=30)
ASSIST_WORKER_RUNNING_TIMEOUT = timedelta(minutes=15)
logger = logging.getLogger(__name__)


def set_application_status(db: Session, job: Job, status: str) -> Job:
    if status not in APPLICATION_STATUSES:
        raise ValueError("Invalid application status")
    if status in {"ready_to_apply", "opened", "applied"}:
        if _is_excluded(job, _score_for_job(db, job.id, None)):
            raise ValueError("Excluded jobs cannot be queued or applied to")
        if job.availability_status not in QUEUEABLE_AVAILABILITY_STATUSES:
            raise ValueError("Only active or unknown-availability jobs can be queued or applied to")
        if job.apply_difficulty == "blocked" or job.apply_strategy == "blocked":
            raise ValueError("Blocked apply routes cannot be queued or applied to")
    job.application_status = status
    if status == "applied":
        job.applied_at = utcnow()
    db.commit()
    db.refresh(job)
    return job


def prepare_applications(db: Session, user: User | None = None) -> tuple[int, list[int]]:
    threshold = minimum_apply_score(db, user)
    rows = _sorted_candidate_rows(
        db,
        select(Job, JobScore)
        .join(JobScore, JobScore.job_id == Job.id)
        .where(
            Job.status != "excluded",
            Job.application_status.not_in(TERMINAL_APPLICATION_STATUSES),
            JobScore.total_score >= Decimal(threshold),
            JobScore.recommendation_tier != "excluded",
        )
        .order_by(JobScore.apply_readiness_score.desc().nulls_last(), JobScore.total_score.desc(), Job.id),
    )
    queued_ids: list[int] = []
    seen: set[int] = set()
    for job, score in rows:
        if user is not None and score.user_id != user.id:
            continue
        if job.id in seen:
            continue
        seen.add(job.id)
        if not _is_queueable(score):
            continue
        _ensure_apply_strategy(job)
        refresh_apply_readiness(job, score)
        if job.apply_difficulty == "blocked" or job.apply_strategy == "blocked":
            continue
        check_job_availability(db, job)
        if job.availability_status not in QUEUEABLE_AVAILABILITY_STATUSES:
            continue
        if job.application_status != "ready_to_apply":
            job.application_status = "ready_to_apply"
            queued_ids.append(job.id)
    db.commit()
    return len(queued_ids), queued_ids


def start_prepare_applications_run(db: Session, user: User) -> tuple[ApplicationPrepareRunStart, bool]:
    active = db.scalar(select(ApplicationPrepareRun).where(ApplicationPrepareRun.status.in_(ACTIVE_RUN_STATUSES)).order_by(ApplicationPrepareRun.id.desc()))
    if active is not None:
        return ApplicationPrepareRunStart(run_id=active.id, status=active.status), False
    run = ApplicationPrepareRun(status="running", last_heartbeat_at=utcnow())
    db.add(run)
    db.commit()
    db.refresh(run)
    logger.info("application_prepare_run_created run_id=%s user_id=%s", run.id, user.id)
    return ApplicationPrepareRunStart(run_id=run.id, status=run.status), True


def get_prepare_applications_run_status(db: Session, run_id: int) -> ApplicationPrepareRunStatus | None:
    run = db.get(ApplicationPrepareRun, run_id)
    if run is None:
        return None
    return ApplicationPrepareRunStatus(
        run_id=run.id,
        status=run.status,
        total=run.total,
        processed=run.processed,
        queued=run.queued,
        skipped=run.skipped,
        failed=run.failed,
        error=run.error,
        started_at=run.started_at,
        finished_at=run.finished_at,
        last_heartbeat_at=run.last_heartbeat_at,
    )


def run_prepare_applications_background(run_id: int, user_id: int) -> None:
    logger.info("application_prepare_background_start run_id=%s user_id=%s", run_id, user_id)
    with SessionLocal() as db:
        run = db.get(ApplicationPrepareRun, run_id)
        user = db.get(User, user_id)
        if run is None or user is None:
            logger.error("application_prepare_missing_context run_id=%s user_id=%s", run_id, user_id)
            return
        try:
            rows = _candidate_application_rows(db, user)
            run.total = len(rows)
            heartbeat(run)
            db.commit()
            seen: set[int] = set()
            for job, score in rows:
                logger.info("application_prepare_job_start run_id=%s job_id=%s", run_id, job.id)
                try:
                    if job.id in seen:
                        run.skipped += 1
                        continue
                    seen.add(job.id)
                    if not _is_queueable(score):
                        run.skipped += 1
                        continue
                    _ensure_apply_strategy(job)
                    refresh_apply_readiness(job, score)
                    if job.apply_difficulty == "blocked" or job.apply_strategy == "blocked":
                        run.skipped += 1
                        continue
                    check_job_availability(db, job)
                    if job.availability_status not in QUEUEABLE_AVAILABILITY_STATUSES:
                        run.skipped += 1
                        continue
                    if job.application_status == "ready_to_apply":
                        run.skipped += 1
                        continue
                    job.application_status = "ready_to_apply"
                    run.queued += 1
                    logger.info("application_prepare_job_queued run_id=%s job_id=%s", run_id, job.id)
                except Exception as exc:  # noqa: BLE001
                    db.rollback()
                    run = db.get(ApplicationPrepareRun, run_id)
                    if run is None:
                        return
                    run.failed += 1
                    run.error = str(exc)
                    logger.exception("application_prepare_job_failed run_id=%s job_id=%s error=%s", run_id, job.id, exc)
                finally:
                    run.processed += 1
                    heartbeat(run)
                    db.commit()
            finish_run(run, "failed" if run.failed and run.queued == 0 else "completed", run.error if run.failed and run.queued == 0 else None)
            db.commit()
            logger.info("application_prepare_run_completed run_id=%s total=%s processed=%s queued=%s skipped=%s failed=%s status=%s", run.id, run.total, run.processed, run.queued, run.skipped, run.failed, run.status)
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            run = db.get(ApplicationPrepareRun, run_id)
            if run is not None:
                finish_run(run, "failed", str(exc))
                db.commit()
            logger.exception("application_prepare_run_failed run_id=%s error=%s", run_id, exc)


def list_applications(db: Session, user: User | None = None) -> list[ApplicationItem]:
    started = time.perf_counter()
    _fail_stale_or_failed_assist_queue(db)
    _fail_stale_browser_startups(db)
    query_started = time.perf_counter()
    score_query = select(
        JobScore.job_id.label("job_id"),
        JobScore.total_score.label("total_score"),
        JobScore.apply_readiness_score.label("apply_readiness_score"),
        JobScore.recommendation.label("recommendation"),
        JobScore.recommendation_tier.label("recommendation_tier"),
    )
    if user is not None:
        score_query = score_query.where(JobScore.user_id == user.id)
    score_subquery = score_query.subquery()

    rows = db.execute(
        select(
            Job.id,
            Job.title,
            Job.company_name,
            Job.location,
            Job.canonical_url,
            Job.application_status,
            Job.availability_status,
            Job.last_checked_at,
            Job.availability_reason,
            Job.apply_strategy,
            Job.apply_difficulty,
            Job.apply_strategy_reason,
            Job.assisted_started_at,
            Job.assisted_result,
            Job.assisted_warnings,
            Job.last_apply_attempt_at,
            score_subquery.c.total_score,
            score_subquery.c.apply_readiness_score,
            score_subquery.c.recommendation,
            score_subquery.c.recommendation_tier,
        )
        .outerjoin(score_subquery, score_subquery.c.job_id == Job.id)
        .where(Job.status != "excluded", Job.application_status.in_(("ready_to_apply", "opened", "failed")))
        .order_by(
            score_subquery.c.apply_readiness_score.desc().nulls_last(),
            score_subquery.c.total_score.desc().nulls_last(),
            _difficulty_order(Job.apply_difficulty),
            Job.id,
        )
    ).all()
    query_duration_ms = int((time.perf_counter() - query_started) * 1000)
    total_duration_ms = int((time.perf_counter() - started) * 1000)
    if query_duration_ms > 500 or total_duration_ms > 1000:
        logger.warning(
            "applications_list_slow count=%s query_duration_ms=%s total_duration_ms=%s user_id=%s",
            len(rows),
            query_duration_ms,
            total_duration_ms,
            getattr(user, "id", None),
        )
    else:
        logger.info(
            "applications_list_done count=%s query_duration_ms=%s total_duration_ms=%s user_id=%s",
            len(rows),
            query_duration_ms,
            total_duration_ms,
            getattr(user, "id", None),
        )
    return [
        ApplicationItem(
            job_id=row.id,
            title=row.title,
            company_name=row.company_name,
            location=row.location,
            apply_url=row.canonical_url,
            application_status=row.application_status,
            availability_status=row.availability_status,
            last_checked_at=row.last_checked_at,
            availability_reason=row.availability_reason,
            apply_strategy=row.apply_strategy,
            apply_difficulty=row.apply_difficulty,
            apply_strategy_reason=row.apply_strategy_reason,
            apply_readiness_score=row.apply_readiness_score,
            assisted_started_at=row.assisted_started_at,
            assisted_result=row.assisted_result,
            assisted_warnings=row.assisted_warnings,
            last_apply_attempt_at=row.last_apply_attempt_at,
            total_score=row.total_score,
            recommendation_tier=row.recommendation_tier,
            recommendation=row.recommendation or _recommendation_from_total(row.total_score),
        )
        for row in rows
    ]


def _fail_stale_browser_startups(db: Session) -> None:
    cutoff = utcnow() - timedelta(seconds=30)
    jobs = db.scalars(select(Job).where(Job.assisted_result.is_not(None), Job.last_apply_attempt_at < cutoff)).all()
    changed = False
    for job in jobs:
        result = job.assisted_result or {}
        if result.get("status") != "running":
            continue
        progress = result.get("progress") if isinstance(result.get("progress"), dict) else {}
        step = progress.get("current_step") or result.get("running_step")
        if step not in {"browser_startup", "browser_launch_start"}:
            continue
        result = {
            **result,
            "status": "failed",
            "final_error": "browser_startup_timeout",
            "warnings": [*result.get("warnings", []), "browser_startup_timeout"],
            "progress": {**progress, "current_step": "browser_startup_timeout", "message": "browser_startup_timeout", "last_heartbeat_at": utcnow().isoformat()},
        }
        job.assisted_result = result
        job.assisted_warnings = result["warnings"]
        job.application_status = "failed"
        changed = True
    if changed:
        db.commit()


def _fail_stale_or_failed_assist_queue(db: Session) -> None:
    queue_cutoff = utcnow() - ASSIST_QUEUE_TIMEOUT
    running_cutoff = utcnow() - ASSIST_WORKER_RUNNING_TIMEOUT
    jobs = db.scalars(select(Job).where(Job.assisted_result.is_not(None))).all()
    changed = False
    for job in jobs:
        result = job.assisted_result or {}
        if result.get("status") not in {"queued", "running"}:
            continue
        progress = result.get("progress") if isinstance(result.get("progress"), dict) else {}
        diagnostics = result.get("jobserve_flow_diagnostics") if isinstance(result.get("jobserve_flow_diagnostics"), dict) else {}
        worker_progress_seen = _assist_worker_progress_seen(result)
        last_heartbeat_at = _parse_assist_datetime(progress.get("last_heartbeat_at"))
        rq_job_id = progress.get("rq_job_id") or diagnostics.get("rq_job_id")
        rq_status = None
        if result.get("status") == "queued" and not worker_progress_seen and job.last_apply_attempt_at and _aware_datetime(job.last_apply_attempt_at) < queue_cutoff:
            result = _assist_queue_failure_result(
                result,
                "stale_queue_timeout",
                "Worker did not pick up the assisted apply job within 2 minutes.",
                _assist_queue_decision_details(
                    job,
                    result,
                    rq_job_id,
                    rq_status,
                    worker_progress_seen,
                    "queued_without_worker_progress_after_timeout",
                    {"queue_age_seconds": int((utcnow() - _aware_datetime(job.last_apply_attempt_at)).total_seconds())},
                ),
            )
        elif _current_assist_progress_step(result) == "worker_started" and last_heartbeat_at and last_heartbeat_at < utcnow() - ASSIST_WORKER_STARTED_HANDOFF_TIMEOUT:
            result = _assist_queue_failure_result(
                result,
                "worker_startup_handoff_timeout",
                "Worker started assisted apply but did not reach loading_application within 30 seconds.",
                _assist_queue_decision_details(
                    job,
                    result,
                    rq_job_id,
                    rq_status,
                    worker_progress_seen,
                    "worker_started_without_loading_application_after_timeout",
                    {"seconds_since_heartbeat": int((utcnow() - last_heartbeat_at).total_seconds()), "last_successful_step": "worker_started"},
                ),
            )
        elif worker_progress_seen and last_heartbeat_at and last_heartbeat_at < running_cutoff:
            result = _assist_queue_failure_result(
                result,
                "worker_progress_timeout",
                "Worker started assisted apply but stopped reporting progress.",
                _assist_queue_decision_details(
                    job,
                    result,
                    rq_job_id,
                    rq_status,
                    worker_progress_seen,
                    "worker_progress_seen_but_heartbeat_stale",
                    {"seconds_since_heartbeat": int((utcnow() - last_heartbeat_at).total_seconds())},
                ),
            )
        else:
            if result.get("status") == "queued":
                _record_assist_queue_decision(result, job, rq_job_id, rq_status, worker_progress_seen, "not_stale")
                job.assisted_result = {**result}
                flag_modified(job, "assisted_result")
                changed = True
            continue
        job.assisted_result = result
        job.assisted_warnings = result["warnings"]
        job.application_status = "failed"
        changed = True
    if changed:
        db.commit()


def _assist_queue_failure_result(result: dict, error: str, message: str, details: dict) -> dict:
    progress = result.get("progress") if isinstance(result.get("progress"), dict) else {}
    diagnostics = result.get("jobserve_flow_diagnostics") if isinstance(result.get("jobserve_flow_diagnostics"), dict) else {}
    warnings = [*result.get("warnings", []), message]
    return {
        **result,
        "status": "failed",
        "final_error": error,
        "running_step": error,
        "warnings": warnings,
        "progress": {
            **progress,
            "current_step": error,
            "message": message,
            "last_heartbeat_at": utcnow().isoformat(),
        },
        "jobserve_flow_diagnostics": {
            **diagnostics,
            "queue_failure": {"error": error, "message": message, **details},
            "queue_diagnostics": details,
        },
        "debug_steps": [*result.get("debug_steps", []), {"step": error, "message": message, **details}][-100:],
    }


def _current_assist_progress_step(result: dict) -> str:
    progress = result.get("progress") if isinstance(result.get("progress"), dict) else {}
    return str(progress.get("current_step") or result.get("running_step") or "")


def _record_assist_queue_decision(result: dict, job: Job, rq_job_id: str | None, rq_status: str | None, worker_progress_seen: bool, reason: str) -> None:
    diagnostics = result.get("jobserve_flow_diagnostics") if isinstance(result.get("jobserve_flow_diagnostics"), dict) else {}
    result["jobserve_flow_diagnostics"] = {
        **diagnostics,
        "queue_diagnostics": _assist_queue_decision_details(job, result, rq_job_id, rq_status, worker_progress_seen, reason, {}),
    }


def _assist_queue_decision_details(job: Job, result: dict, rq_job_id: str | None, rq_status: str | None, worker_progress_seen: bool, reason: str, extra: dict) -> dict:
    progress = result.get("progress") if isinstance(result.get("progress"), dict) else {}
    return {
        "rq_job_id": rq_job_id,
        "rq_status": rq_status,
        "queued_at": job.last_apply_attempt_at.isoformat() if job.last_apply_attempt_at else None,
        "started_at": job.assisted_started_at.isoformat() if job.assisted_started_at else None,
        "last_progress_step": progress.get("current_step") or result.get("running_step"),
        "last_heartbeat_at": progress.get("last_heartbeat_at"),
        "worker_progress_seen": worker_progress_seen,
        "stale_queue_decision_reason": reason,
        **extra,
    }


def _assist_worker_progress_seen(result: dict) -> bool:
    progress = result.get("progress") if isinstance(result.get("progress"), dict) else {}
    step = str(progress.get("current_step") or result.get("running_step") or "")
    if step and step != "queued":
        return True
    debug_steps = result.get("debug_steps") if isinstance(result.get("debug_steps"), list) else []
    worker_markers = {
        "worker_started",
        "worker_handoff_entered",
        "db_session_create_start",
        "db_session_create_done",
        "assist_apply_application_entered",
        "loading_application",
        "application_loaded",
        "loading_job",
        "job_loaded",
        "loading_profile",
        "profile_loaded",
        "resolving_job_url",
        "job_url_resolved",
        "browser_startup",
        "browser_launch_start",
        "browser_launch_success",
        "page_created",
        "navigating_to_job_url",
        "job_page_loaded",
        "jobserve_flow_started",
        "jobserve_apply_button_clicked",
        "jobserve_apply_modal_wait_complete",
        "apply_button_clicked",
        "modal_wait_complete",
        "before_filling",
        "email_filled",
        "confirmation_checked",
        "working_status_selected",
        "cv_upload_started",
        "cv_uploaded",
        "final_apply_clicked",
        "submitted_message_seen",
        "account_toggle_disabled",
        "modal_closed",
        "jobserve_apply_email_filled",
        "jobserve_apply_cv_uploaded",
    }
    for item in debug_steps:
        if not isinstance(item, dict):
            continue
        name = str(item.get("step") or "")
        if name in worker_markers or name.startswith("jobserve_"):
            return True
    diagnostics = result.get("jobserve_flow_diagnostics") if isinstance(result.get("jobserve_flow_diagnostics"), dict) else {}
    return any(bool(diagnostics.get(key)) for key in ["apply_button_clicked", "job_application_modal_found", "cv_upload_input_detected", "email_filled", "cv_uploaded"])


def _parse_assist_datetime(value) -> object | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return _aware_datetime(parsed)


def _aware_datetime(value):
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _candidate_application_rows(db: Session, user: User | None = None):
    threshold = minimum_apply_score(db, user)
    query = (
        select(Job, JobScore)
        .join(JobScore, JobScore.job_id == Job.id)
        .where(
            Job.status != "excluded",
            Job.application_status.not_in(TERMINAL_APPLICATION_STATUSES),
            JobScore.total_score >= Decimal(threshold),
            JobScore.recommendation_tier != "excluded",
        )
        .order_by(JobScore.apply_readiness_score.desc().nulls_last(), JobScore.total_score.desc(), _difficulty_order(Job.apply_difficulty), Job.id)
    )
    if user is not None:
        query = query.where(JobScore.user_id == user.id)
    return _sorted_candidate_rows(db, query)


def _is_queueable(score: JobScore) -> bool:
    recommendation = score.recommendation or _recommendation_from_total(score.total_score)
    return recommendation in QUEUEABLE_RECOMMENDATIONS


def _is_excluded(job: Job, score: JobScore | None) -> bool:
    return job.status == "excluded" or (score is not None and score.recommendation_tier == "excluded")


def _score_for_job(db: Session, job_id: int, user: User | None) -> JobScore | None:
    query = select(JobScore).where(JobScore.job_id == job_id).order_by(JobScore.scored_at.desc())
    if user is not None:
        query = query.where(JobScore.user_id == user.id)
    return db.scalar(query)


def _recommendation_from_total(total_score: Decimal | None) -> str | None:
    if total_score is None:
        return None
    if total_score >= Decimal("70"):
        return "apply"
    if total_score >= Decimal("50"):
        return "maybe"
    return "skip"


def _ensure_apply_strategy(job: Job) -> None:
    if (job.apply_strategy == "unknown" and job.apply_difficulty == "unknown") or job.apply_strategy == "jobserve_apply":
        classification = classify_job(job)
        job.apply_strategy = classification.strategy
        job.apply_difficulty = classification.difficulty
        job.apply_strategy_reason = classification.reason


def _difficulty_order(column):
    from sqlalchemy import case

    return case((column == "easy", 0), (column == "medium", 1), (column == "hard", 2), (column == "unknown", 3), else_=4)


def _sorted_candidate_rows(db: Session, query):
    rows = db.execute(query).all()
    for job, score in rows:
        _ensure_apply_strategy(job)
        refresh_apply_readiness(job, score)
    db.flush()
    return sorted(
        rows,
        key=lambda row: (
            -(float(row[1].apply_readiness_score or 0)),
            -(float(row[1].total_score or 0)),
            _difficulty_rank(row[0].apply_difficulty),
            row[0].id,
        ),
    )


def _difficulty_rank(value: str | None) -> int:
    return {"easy": 0, "medium": 1, "hard": 2, "unknown": 3, "blocked": 4}.get(value or "unknown", 3)


def minimum_apply_score(db: Session, user: User | None) -> int:
    if user is None:
        return 80
    profile = get_profile(db, user)
    value = getattr(profile, "minimum_apply_score", None) if profile else None
    return int(value or 80)
