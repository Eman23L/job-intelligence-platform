"""Find jobs: the jobs found for the roles a person wants, best match first, and what they've done with each."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Application, Job, JobScore, User
from app.schemas.database import FindResult, FoundJob, SearchState
from app.services.applications import CLOSED_AVAILABILITY, has_cv, role_key, salary_text, source_for_url, title_fits_roles
from app.services.discovery import feeds_configured, next_run_time, wanted_location, wanted_roles
from app.services.profile import get_profile
from app.services.queue import queue_enabled

# Jobs still being listed within this window show up; older ones have usually closed.
FIND_WINDOW_DAYS = 14
NEW_WITHIN = timedelta(hours=24)
# A search that hasn't finished after this long is treated as over (the worker may have restarted).
SEARCH_TIMEOUT = timedelta(hours=1)
STATUS_ORDER = {None: 0, "to_apply": 1, "applied": 2, "interviewing": 2, "offer": 2, "rejected": 2}


def find_jobs(db: Session, user: User) -> FindResult:
    profile = get_profile(db, user)
    roles = wanted_roles(profile)
    now = datetime.now(tz=timezone.utc)
    started = _aware(profile.search_started_at) if profile else None
    finished = _aware(profile.search_finished_at) if profile else None
    search = SearchState(
        roles=roles,
        location=wanted_location(profile),
        remote_only=bool(profile.search_remote_only) if profile else False,
        searching=bool(started and (finished is None or finished < started) and now - started < SEARCH_TIMEOUT),
        last_finished_at=finished,
        next_run_at=next_run_time(now) if queue_enabled() else None,
        feeds=feeds_configured(),
    )
    if not roles:
        return FindResult(has_cv=has_cv(db, user), search=search, jobs=[])

    rows = db.execute(
        select(Job, JobScore, Application)
        .outerjoin(JobScore, (JobScore.job_id == Job.id) & (JobScore.user_id == user.id))
        .outerjoin(Application, (Application.job_id == Job.id) & (Application.user_id == user.id))
        .where(
            Job.last_seen_at >= now - timedelta(days=FIND_WINDOW_DAYS),
            Job.status != "excluded",
            Job.availability_status.not_in(CLOSED_AVAILABILITY),
        )
    ).all()
    # Keep the copy the person acted on (else the best scoring one) when a role is listed on several boards.
    rows.sort(key=lambda row: (row[2] is None, -float(row[1].total_score if row[1] else -1)))
    seen: set[tuple[str, str]] = set()
    jobs: list[FoundJob] = []
    for job, score, application in rows:
        status = application.status if application else None
        if status == "skipped" or not title_fits_roles(job.title, roles):
            continue
        key = role_key(job.title, job.company_name)
        if key in seen:
            continue
        seen.add(key)
        first_seen = _aware(job.first_seen_at) or now
        jobs.append(
            FoundJob(
                job_id=job.id,
                title=job.title,
                company=job.company_name,
                location=job.location,
                salary=salary_text(job),
                source=source_for_url(job.canonical_url),
                url=job.canonical_url,
                first_seen_at=first_seen,
                is_new=now - first_seen < NEW_WITHIN,
                total_score=score.total_score if score else None,
                recommendation_tier=score.recommendation_tier if score else None,
                application_id=application.id if application else None,
                status=status,
                applied_at=application.applied_at if application else None,
            )
        )
    jobs.sort(key=lambda job: (STATUS_ORDER.get(job.status, 2), -float(job.total_score or -1)))
    return FindResult(has_cv=has_cv(db, user), search=search, jobs=jobs)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
