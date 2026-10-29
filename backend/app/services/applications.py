from __future__ import annotations

from datetime import datetime, timezone
import re
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db.models import Application, Job, JobScore, User
from app.schemas.database import (
    ApplicationCreate,
    ApplicationRead,
    ApplicationUpdate,
    ApplyPack,
    ApplyPackCandidate,
    ApplyPackJob,
)
from app.services.apply_strategy import classify_job, refresh_apply_readiness
from app.services.profile import get_profile

# "suggested" = found by the daily search and waiting in New matches for approval.
APPLICATION_STATUSES = {"suggested", "to_apply", "applied", "interviewing", "offer", "rejected", "skipped"}
# Statuses that mean the person has actually sent an application.
SUBMITTED_STATUSES = {"applied", "interviewing", "offer", "rejected"}
SOURCES = {"jobserve", "reed", "adzuna", "linkedin", "indeed", "company_site", "other"}
# Listings we know are gone (or now show a different job) shouldn't be applied to.
CLOSED_AVAILABILITY = {"expired", "unavailable", "replaced", "redirected"}
DEFAULT_MINIMUM_APPLY_SCORE = 80
# Words that say nothing about the kind of role, so a title can't match a target role on these alone.
GENERIC_TITLE_WORDS = {
    "senior", "junior", "lead", "principal", "staff", "head", "mid", "level", "graduate", "trainee", "associate",
    "engineer", "engineering", "developer", "development", "specialist", "consultant", "manager", "analyst",
    "architect", "officer", "contract", "permanent", "remote", "hybrid", "and", "of", "the", "i", "ii", "iii",
}
# Title words that count as the same kind of role as a target-role word.
ROLE_WORD_SYNONYMS = {
    "ai": {"ai", "ml", "machine", "llm", "genai", "artificial"},
    "python": {"python", "django", "fastapi", "flask"},
    "software": {"software", "backend", "fullstack", "full", "application", "web", "platform"},
    "data": {"data", "analytics", "bi", "etl", "database", "sql"},
    "automation": {"automation", "rpa", "power", "workflow", "integration"},
}


class ApplicationError(ValueError):
    """A problem with an application request that is safe to show to the user."""


def minimum_apply_score(db: Session, user: User) -> int:
    profile = get_profile(db, user)
    value = getattr(profile, "minimum_apply_score", None) if profile else None
    return int(value or DEFAULT_MINIMUM_APPLY_SCORE)


def has_cv(db: Session, user: User) -> bool:
    """Jobs are matched against the CV text, so nothing can be suggested until it's saved."""
    profile = get_profile(db, user)
    return bool(profile and (profile.cv_text or "").strip())


def list_applications(db: Session, user: User) -> list[ApplicationRead]:
    rows = db.execute(
        select(Application, JobScore, Job)
        .outerjoin(Job, Job.id == Application.job_id)
        .outerjoin(JobScore, (JobScore.job_id == Application.job_id) & (JobScore.user_id == user.id))
        .where(Application.user_id == user.id)
        .order_by(Application.updated_at.desc(), Application.id.desc())
    ).all()
    return [_read(application, score, job) for application, score, job in rows]


def queue_top_matches(db: Session, user: User, minimum_score: int | None = None) -> list[int]:
    """Add the person's best-scoring open jobs to their apply list, easiest first.

    Returns the ids of the jobs added. Jobs with no way to apply (a blocked route) are skipped.
    """
    threshold = minimum_score if minimum_score is not None else minimum_apply_score(db, user)
    already = select(Application.job_id).where(Application.user_id == user.id, Application.job_id.is_not(None))
    rows = db.execute(
        select(Job, JobScore)
        .join(JobScore, (JobScore.job_id == Job.id) & (JobScore.user_id == user.id))
        .where(
            JobScore.total_score >= threshold,
            or_(JobScore.recommendation_tier.is_(None), JobScore.recommendation_tier != "excluded"),
            Job.status != "excluded",
            Job.availability_status.not_in(CLOSED_AVAILABILITY),
            Job.id.not_in(already),
        )
    ).all()
    for job, score in rows:
        _ensure_apply_strategy(job)
        refresh_apply_readiness(job, score)
    candidates = sorted(
        (row for row in rows if row[0].apply_strategy != "blocked" and row[0].apply_difficulty != "blocked"),
        key=lambda row: (
            -float(row[1].apply_readiness_score or 0),
            -float(row[1].total_score or 0),
            _DIFFICULTY_RANK.get(row[0].apply_difficulty or "unknown", 3),
            row[0].id,
        ),
    )
    for job, _score in candidates:
        db.add(_from_job(user, job, status="to_apply"))
    db.commit()
    return [job.id for job, _score in candidates]


def suggest_new_matches(db: Session, user: User, job_ids: list[int]) -> int:
    """Put newly found jobs that score well for this person into their New matches. Returns how many were added."""
    if not job_ids:
        return 0
    threshold = minimum_apply_score(db, user)
    profile = get_profile(db, user)
    roles = list(getattr(profile, "preferred_roles", None) or [])
    listed = select(Application.job_id).where(Application.user_id == user.id, Application.job_id.is_not(None))
    rows = db.execute(
        select(Job, JobScore)
        .join(JobScore, (JobScore.job_id == Job.id) & (JobScore.user_id == user.id))
        .where(
            Job.id.in_(job_ids),
            JobScore.total_score >= threshold,
            or_(JobScore.recommendation_tier.is_(None), JobScore.recommendation_tier != "excluded"),
            Job.status != "excluded",
            Job.availability_status.not_in(CLOSED_AVAILABILITY),
            Job.id.not_in(listed),
        )
        .order_by(JobScore.total_score.desc())
    ).all()
    seen = _listed_roles(db, user)
    added = 0
    for job, _score in rows:
        # Keyword searches return plenty of unrelated roles; only suggest titles that fit a target role.
        if not title_fits_roles(job.title, roles):
            continue
        # The same role is often advertised on several boards; suggest it once.
        role = _role_key(job.title, job.company_name)
        if role in seen:
            continue
        seen.add(role)
        db.add(_from_job(user, job, status="suggested"))
        added += 1
    db.commit()
    return added


def import_suggestions(db: Session, user: User, jobs: list[dict]) -> tuple[int, int]:
    """Add jobs found elsewhere (LinkedIn and Indeed alert emails) to New matches. Returns (added, skipped)."""
    seen_urls = {_url_key(url) for url in db.scalars(select(Application.url).where(Application.user_id == user.id, Application.url.is_not(None)))}
    seen_roles = _listed_roles(db, user)
    added = skipped = 0
    for item in jobs:
        title = (item.get("title") or "").strip()
        url_key = _url_key(item.get("url"))
        role = _role_key(title, item.get("company"))
        if not title or (url_key and url_key in seen_urls) or role in seen_roles:
            skipped += 1
            continue
        seen_urls.add(url_key)
        seen_roles.add(role)
        db.add(
            Application(
                user_id=user.id,
                source=item.get("source") if item.get("source") in SOURCES else "other",
                title=title[:500],
                company=_clean(item.get("company"), 255),
                location=_clean(item.get("location"), 255),
                url=_clean(item.get("url"), 2000),
                status="suggested",
                notes=_clean(item.get("reason"), 2000),
            )
        )
        added += 1
    db.commit()
    return added, skipped


def decide_suggestions(db: Session, user: User, *, approve: bool, application_ids: list[int] | None = None) -> int:
    """Approve New matches onto the apply list, or dismiss them to Skipped. None means all of them."""
    query = select(Application).where(Application.user_id == user.id, Application.status == "suggested")
    if application_ids is not None:
        query = query.where(Application.id.in_(application_ids))
    rows = db.scalars(query).all()
    for application in rows:
        application.status = "to_apply" if approve else "skipped"
    db.commit()
    return len(rows)


def counts(db: Session, user: User) -> dict[str, int]:
    rows = db.execute(
        select(Application.status, func.count()).where(Application.user_id == user.id).group_by(Application.status)
    ).all()
    return {status: total for status, total in rows}


def jobs_to_check(db: Session, user: User) -> list[int]:
    """Job ids on the person's apply list, for checking the listings are still open."""
    return list(
        db.scalars(
            select(Application.job_id).where(
                Application.user_id == user.id, Application.status == "to_apply", Application.job_id.is_not(None)
            )
        ).all()
    )


def skip_closed(db: Session, user: User) -> int:
    """Move apply-list jobs whose listing has closed to Skipped. Returns how many moved."""
    rows = db.scalars(
        select(Application)
        .join(Job, Job.id == Application.job_id)
        .where(Application.user_id == user.id, Application.status == "to_apply", Job.availability_status.in_(CLOSED_AVAILABILITY))
    ).all()
    for application in rows:
        application.status = "skipped"
        note = "The listing had closed when checked."
        application.notes = f"{application.notes}\n{note}" if application.notes else note
    db.commit()
    return len(rows)


def add_application(db: Session, user: User, payload: ApplicationCreate) -> ApplicationRead:
    status = _check_status(payload.status)
    if payload.job_id is not None:
        job = db.get(Job, payload.job_id)
        if job is None:
            raise ApplicationError("That job no longer exists.")
        existing = _for_job(db, user, job.id)
        if existing is not None:
            # Adding a job twice just moves it to the requested status.
            return update_application(db, user, existing, ApplicationUpdate(status=status, notes=payload.notes))
        application = _from_job(user, job, status=status)
    else:
        title = (payload.title or "").strip()
        if not title:
            raise ApplicationError("Give the job a title.")
        application = Application(
            user_id=user.id,
            source=payload.source if payload.source in SOURCES else "other",
            title=title[:500],
            company=_clean(payload.company, 255),
            location=_clean(payload.location, 255),
            url=_clean(payload.url, 2000),
            status=status,
        )
    application.notes = _clean(payload.notes, 10_000)
    if status in SUBMITTED_STATUSES:
        application.applied_at = _now()
    db.add(application)
    db.commit()
    return get_application(db, user, application.id)


def set_job_status(db: Session, user: User, job: Job, status: str) -> ApplicationRead:
    """Mark a job the app found as queued, applied or skipped for this person."""
    return add_application(db, user, ApplicationCreate(job_id=job.id, status=status))


def update_application(db: Session, user: User, application: Application, payload: ApplicationUpdate) -> ApplicationRead:
    if payload.status is not None:
        status = _check_status(payload.status)
        if status in SUBMITTED_STATUSES and application.applied_at is None:
            application.applied_at = _now()
        if status == "to_apply":
            application.applied_at = None
        application.status = status
    if payload.notes is not None:
        application.notes = _clean(payload.notes, 10_000)
    db.commit()
    return get_application(db, user, application.id)


def owned_application(db: Session, user: User, application_id: int) -> Application | None:
    application = db.get(Application, application_id)
    if application is None or application.user_id != user.id:
        return None
    return application


def get_application(db: Session, user: User, application_id: int) -> ApplicationRead:
    row = db.execute(
        select(Application, JobScore, Job)
        .outerjoin(Job, Job.id == Application.job_id)
        .outerjoin(JobScore, (JobScore.job_id == Application.job_id) & (JobScore.user_id == user.id))
        .where(Application.id == application_id, Application.user_id == user.id)
    ).one()
    return _read(*row)


def delete_application(db: Session, application: Application) -> None:
    db.delete(application)
    db.commit()


def apply_pack(db: Session, user: User, application: Application) -> ApplyPack:
    """Everything Claude needs to fill in this application in the person's browser."""
    job = db.get(Job, application.job_id) if application.job_id else None
    profile = get_profile(db, user)
    return ApplyPack(
        application=get_application(db, user, application.id),
        job=ApplyPackJob(
            title=application.title,
            company=application.company,
            location=application.location,
            url=application.url or (job.canonical_url if job else None),
            salary=_salary_text(job) if job else None,
            posted_at=job.posted_at if job else None,
            description=job.description_text if job else None,
        ),
        candidate=ApplyPackCandidate(
            first_name=getattr(profile, "first_name", None),
            last_name=getattr(profile, "last_name", None),
            email=getattr(profile, "email", None) or user.email,
            phone=getattr(profile, "phone", None),
            address=getattr(profile, "address", None),
            country=getattr(profile, "country", None),
            work_status_uk=getattr(profile, "work_status_uk", None),
            sponsorship_required=bool(getattr(profile, "sponsorship_required", False)),
            availability_notice=getattr(profile, "availability_notice", None),
            salary_expectation=getattr(profile, "salary_expectation", None),
            travel_distance=getattr(profile, "travel_distance", None),
            cv_file_name=getattr(profile, "cv_file_name", None),
            cv_text=getattr(profile, "cv_text", None),
        ),
    )


def _from_job(user: User, job: Job, *, status: str) -> Application:
    return Application(
        user_id=user.id,
        job_id=job.id,
        source=_source_for_url(job.canonical_url),
        title=job.title,
        company=job.company_name,
        location=job.location,
        url=job.canonical_url,
        status=status,
        applied_at=_now() if status in SUBMITTED_STATUSES else None,
    )


def title_fits_roles(title: str | None, roles: list[str]) -> bool:
    """True when the job title shares a meaningful word (or close synonym) with one of the target roles."""
    if not roles:
        return True
    title_words = set(re.findall(r"[a-z0-9]+", (title or "").lower()))
    for role in roles:
        for word in set(re.findall(r"[a-z0-9]+", role.lower())) - GENERIC_TITLE_WORDS:
            if title_words & ROLE_WORD_SYNONYMS.get(word, {word}):
                return True
    return False


def _source_for_url(url: str | None) -> str:
    lowered = (url or "").lower()
    for marker, source in (("jobserve.", "jobserve"), ("reed.co.uk", "reed"), ("adzuna.", "adzuna"), ("linkedin.com", "linkedin"), ("indeed.", "indeed")):
        if marker in lowered:
            return source
    return "company_site"


def _url_key(url: str | None) -> str:
    """A stable key for a job link: the LinkedIn or Indeed job id when there is one, else the bare URL."""
    if not url:
        return ""
    parts = urlsplit(url.strip())
    linkedin = re.search(r"/jobs/view/(?:[^/]*-)?(\d+)", parts.path)
    if "linkedin.com" in parts.netloc and linkedin:
        return f"linkedin:{linkedin.group(1)}"
    indeed_id = parse_qs(parts.query).get("jk") or parse_qs(parts.query).get("vjk")
    if "indeed." in parts.netloc and indeed_id:
        return f"indeed:{indeed_id[0]}"
    return f"{parts.netloc.lower()}{parts.path.rstrip('/')}"


def _role_key(title: str | None, company: str | None) -> tuple[str, str]:
    def norm(value: str | None) -> str:
        return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()

    return norm(title), norm(company)


def _listed_roles(db: Session, user: User) -> set[tuple[str, str]]:
    rows = db.execute(select(Application.title, Application.company).where(Application.user_id == user.id)).all()
    return {_role_key(title, company) for title, company in rows}


_DIFFICULTY_RANK = {"easy": 0, "medium": 1, "hard": 2, "unknown": 3, "blocked": 4}


def _ensure_apply_strategy(job: Job) -> None:
    if (job.apply_strategy == "unknown" and job.apply_difficulty == "unknown") or job.apply_strategy == "jobserve_apply":
        classification = classify_job(job)
        job.apply_strategy = classification.strategy
        job.apply_difficulty = classification.difficulty
        job.apply_strategy_reason = classification.reason


def _for_job(db: Session, user: User, job_id: int) -> Application | None:
    return db.scalar(select(Application).where(Application.user_id == user.id, Application.job_id == job_id))


def _read(application: Application, score: JobScore | None, job: Job | None) -> ApplicationRead:
    item = ApplicationRead.model_validate(application)
    item.total_score = score.total_score if score else None
    item.recommendation_tier = score.recommendation_tier if score else None
    item.availability_status = job.availability_status if job else None
    return item


def _check_status(status: str) -> str:
    if status not in APPLICATION_STATUSES:
        raise ApplicationError("Unknown application status.")
    return status


def _clean(value: str | None, limit: int) -> str | None:
    text = (value or "").strip()
    return text[:limit] or None


def _salary_text(job: Job) -> str | None:
    low, high = job.salary_min, job.salary_max
    if low is None and high is None:
        return None
    currency = "£" if (job.salary_currency or "GBP") == "GBP" else f"{job.salary_currency} "
    period = f" per {job.salary_period}" if job.salary_period else ""
    if low is not None and high is not None and low != high:
        return f"{currency}{low:,.0f} – {currency}{high:,.0f}{period}"
    return f"{currency}{(low if low is not None else high):,.0f}{period}"


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)
