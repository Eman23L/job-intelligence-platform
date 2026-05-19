import re
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ExcludedTechnology, Job, JobAnalysis, JobSkill
from app.services.normalisation import classify_role_family, detect_seniority, normalise_job_fields
from app.services.skills import detect_excluded_technologies, extract_skills


def analyse_job(db: Session, job: Job) -> JobAnalysis:
    description = job.description_text or ""
    normalised = normalise_job_fields(
        title=job.title,
        company_name=job.company_name,
        location=job.location,
        remote_type=job.remote_type,
        employment_type=job.employment_type,
        salary_min=job.salary_min,
        salary_max=job.salary_max,
        salary_currency=job.salary_currency,
        salary_min_raw=job.salary_min_raw,
        salary_max_raw=job.salary_max_raw,
        salary_period=job.salary_period,
        normalized_annual_min=job.normalized_annual_min,
        normalized_annual_max=job.normalized_annual_max,
        posted_date=job.posted_at,
        canonical_url=job.canonical_url,
        description_text=description,
    )
    job.title = normalised.title
    job.company_name = normalised.company_name
    job.location = normalised.location
    job.remote_type = normalised.remote_type
    job.employment_type = normalised.employment_type
    job.salary_min = normalised.salary_min
    job.salary_max = normalised.salary_max
    job.salary_currency = normalised.salary_currency
    job.salary_min_raw = normalised.salary_min_raw
    job.salary_max_raw = normalised.salary_max_raw
    job.salary_period = normalised.salary_period
    job.normalized_annual_min = normalised.normalized_annual_min
    job.normalized_annual_max = normalised.normalized_annual_max
    job.posted_at = normalised.posted_at
    job.canonical_url = normalised.canonical_url
    job.content_hash = normalised.content_hash

    skills = extract_skills(description)
    db.query(JobSkill).filter(JobSkill.job_id == job.id).delete()
    for skill in skills:
        db.add(
            JobSkill(
                job_id=job.id,
                skill_name=skill.name,
                skill_category=skill.category,
                importance=skill.importance,
                evidence_text=skill.evidence_text,
            )
        )

    excluded_names = db.scalars(select(ExcludedTechnology.name).order_by(ExcludedTechnology.name)).all()
    excluded_mentions = detect_excluded_technologies(description, list(excluded_names))
    red_flags = [
        f"{mention.name}: {mention.severity} ({mention.evidence_text})" for mention in excluded_mentions
    ]
    role_family = classify_role_family(job.title, description)
    analysis = db.scalar(select(JobAnalysis).where(JobAnalysis.job_id == job.id))
    if analysis is None:
        analysis = JobAnalysis(job_id=job.id)
        db.add(analysis)

    analysis.seniority_level = detect_seniority(job.title, description)
    analysis.role_family = role_family
    analysis.role_focus = detect_role_focus(job.title, description)
    analysis.tools_detected = [skill.name for skill in skills]
    analysis.responsibilities = extract_responsibilities(description)
    analysis.requirements = extract_requirements(description)
    analysis.nice_to_haves = extract_nice_to_haves(description)
    analysis.red_flags = red_flags
    analysis.analysed_at = datetime.now(tz=timezone.utc)
    db.commit()
    db.refresh(analysis)
    return analysis


def analyse_all_jobs(db: Session) -> dict[str, int]:
    jobs = db.scalars(select(Job).order_by(Job.id)).all()
    for job in jobs:
        analyse_job(db, job)
    return {"jobs_analyzed": len(jobs)}


def detect_role_focus(title: str, description: str) -> str | None:
    text = f"{title} {description}".lower()
    focuses = [
        ("AI automation", ["llm", "rag", "ai automation", "agents"]),
        ("workflow automation", ["workflow automation", "orchestration"]),
        ("process automation", ["process automation", "business process"]),
        ("internal tools", ["internal tools", "internal tooling"]),
        ("analytics engineering", ["dbt", "analytics engineer", "data modelling"]),
        ("data platform", ["data platform", "lakehouse", "databricks", "fabric"]),
        ("data pipelines", ["data pipeline", "etl", "elt"]),
    ]
    for focus, terms in focuses:
        if any(term in text for term in terms):
            return focus
    return None


def extract_responsibilities(text: str) -> list[str]:
    return _extract_marked_items(text, ("responsibilities", "you will", "what you'll do", "duties"))


def extract_requirements(text: str) -> list[str]:
    return _extract_marked_items(text, ("requirements", "required", "essential", "must have"))


def extract_nice_to_haves(text: str) -> list[str]:
    return _extract_marked_items(text, ("nice to have", "desirable", "preferred", "bonus"))


def _extract_marked_items(text: str, markers: tuple[str, ...]) -> list[str]:
    sentences = [sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", text))]
    matches = []
    for sentence in sentences:
        lowered = sentence.lower()
        if any(marker in lowered for marker in markers):
            matches.append(sentence)
    return matches[:10]
