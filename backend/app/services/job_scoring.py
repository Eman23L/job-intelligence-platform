import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Job, JobAnalysis, JobScore, JobSkill, MissingSkill, User, UserProfile
from app.services.profile import get_profile


WEIGHTS = {
    "skill_match": 0.35,
    "experience_relevance": 0.25,
    "role_family_fit": 0.15,
    "location_remote_fit": 0.10,
    "salary_fit": 0.10,
    "confidence": 0.05,
}


@dataclass(frozen=True)
class ScoreResult:
    total_score: float
    tier: str
    recommendation: str
    matched_skills: list[str]
    missing_skills: list[str]
    evidence: list[str]
    risks: list[str]
    confidence_score: float
    breakdown: dict[str, float]
    gates: list[str]


def rescore_jobs(db: Session, user: User | None = None) -> dict[str, int]:
    user = user or _default_user(db)
    profile = get_profile(db, user)
    if profile is None:
        raise ValueError("No saved profile found. Save CV/profile before rescoring jobs.")

    jobs = db.scalars(select(Job).where(Job.status != "excluded").order_by(Job.id)).all()
    scored = 0
    for job in jobs:
        score_job_against_profile(db, job, user, profile)
        scored += 1
    skipped = db.scalar(select(func.count(Job.id)).where(Job.status == "excluded")) or 0
    db.commit()
    return {"jobs_scored": scored, "jobs_skipped": skipped}


def score_job_against_profile(db: Session, job: Job, user: User, profile: UserProfile) -> JobScore:
    if job.status == "excluded":
        raise ValueError("Excluded jobs are not scored.")

    result = build_scorecard(db, job, profile)
    score = db.scalar(select(JobScore).where(JobScore.job_id == job.id, JobScore.user_id == user.id))
    if score is None:
        score = JobScore(job_id=job.id, user_id=user.id, total_score=Decimal("0"))
        db.add(score)

    score.total_score = _decimal(result.total_score)
    score.role_match_score = _decimal(result.breakdown["role_family_fit"])
    score.skill_match_score = _decimal(result.breakdown["skill_match"])
    score.experience_score = _decimal(result.breakdown["experience_relevance"])
    score.salary_score = _decimal(result.breakdown["salary_fit"])
    score.location_score = _decimal(result.breakdown["location_remote_fit"])
    score.freshness_score = _decimal(result.breakdown["confidence"])
    score.missing_skill_penalty = _decimal(max(0, 35 - result.breakdown["skill_match"]))
    score.recommendation_tier = result.tier
    score.explanation = json.dumps(_scorecard_payload(job, result), sort_keys=True)
    score.scored_at = datetime.now(tz=timezone.utc)

    _refresh_missing_skills(db, job, user, result.missing_skills)
    db.flush()
    return score


def build_scorecard(db: Session, job: Job, profile: UserProfile) -> ScoreResult:
    analysis = db.scalar(select(JobAnalysis).where(JobAnalysis.job_id == job.id))
    job_skills = _job_skills(db, job, analysis)
    profile_skills = {_norm(skill) for skill in profile.skills or []}
    matched = sorted({skill for skill in job_skills if _norm(skill) in profile_skills})
    missing = sorted({skill for skill in job_skills if _norm(skill) not in profile_skills})
    text = _job_text(job, analysis)

    gates, gate_risks = _deterministic_gates(job, profile, text)
    skill_score = 100.0 if not job_skills else 100.0 * (len(matched) / len(set(job_skills)))
    experience_score = _experience_relevance(profile, text)
    role_score = _role_family_fit(profile, job, analysis)
    location_score = _location_remote_fit(profile, job)
    salary_score, salary_risks = _salary_fit(profile, job)
    confidence = _confidence(job, analysis, job_skills)

    breakdown = {
        "skill_match": round(skill_score * WEIGHTS["skill_match"], 2),
        "experience_relevance": round(experience_score * WEIGHTS["experience_relevance"], 2),
        "role_family_fit": round(role_score * WEIGHTS["role_family_fit"], 2),
        "location_remote_fit": round(location_score * WEIGHTS["location_remote_fit"], 2),
        "salary_fit": round(salary_score * WEIGHTS["salary_fit"], 2),
        "confidence": round(confidence * WEIGHTS["confidence"], 2),
    }
    total = round(sum(breakdown.values()), 2)
    risks = gate_risks + salary_risks
    if gates:
        total = min(total, 49.0)
    tier = score_tier(total)
    recommendation = _recommendation(total, gates)
    evidence = _evidence(job, analysis, matched, profile)
    return ScoreResult(
        total_score=total,
        tier=tier,
        recommendation=recommendation,
        matched_skills=matched,
        missing_skills=missing,
        evidence=evidence,
        risks=risks,
        confidence_score=round(confidence, 2),
        breakdown=breakdown,
        gates=gates,
    )


def score_tier(total_score: float) -> str:
    if total_score >= 85:
        return "Excellent match"
    if total_score >= 70:
        return "Strong match"
    if total_score >= 50:
        return "Possible match"
    return "Weak match"


def scorecard_for_job(db: Session, job: Job) -> dict[str, Any]:
    score = db.scalar(select(JobScore).where(JobScore.job_id == job.id).order_by(JobScore.scored_at.desc()))
    if score and score.explanation:
        try:
            return json.loads(score.explanation)
        except json.JSONDecodeError:
            pass
    user = _default_user(db)
    profile = get_profile(db, user)
    if profile is None:
        raise ValueError("No saved profile found. Save CV/profile before requesting a scorecard.")
    result = build_scorecard(db, job, profile)
    return _scorecard_payload(job, result)


def recommendation_from_score(score: JobScore | None) -> str | None:
    if score is None or not score.explanation:
        return None
    try:
        payload = json.loads(score.explanation)
    except json.JSONDecodeError:
        return None
    recommendation = payload.get("recommendation")
    return recommendation if isinstance(recommendation, str) else None


def _deterministic_gates(job: Job, profile: UserProfile, text: str) -> tuple[list[str], list[str]]:
    gates = []
    risks = []
    auth = " ".join(str(value) for value in (profile.preferences or {}).values()).lower()
    if "sponsorship" in text and "not require sponsorship" not in auth and "no sponsorship" not in auth:
        gates.append("work_authorization")
        risks.append("Job mentions sponsorship/work authorisation but profile does not confirm no sponsorship requirement.")
    if re.search(r"\bsc\s+clear", text) and "sc cleared" not in auth:
        gates.append("required_clearance")
        risks.append("Job appears to require SC clearance, but profile does not show SC Cleared.")
    if re.search(r"\bbpss\b", text) and "bpss cleared" not in auth:
        risks.append("Job mentions BPSS; profile does not show BPSS Cleared.")
    if _location_remote_fit(profile, job) == 0:
        gates.append("location_remote")
        risks.append("Location/remote arrangement does not match saved profile preferences.")
    return gates, risks


def _job_skills(db: Session, job: Job, analysis: JobAnalysis | None) -> list[str]:
    rows = db.scalars(select(JobSkill).where(JobSkill.job_id == job.id)).all()
    skills = [row.skill_name for row in rows]
    if analysis and analysis.tools_detected:
        skills.extend(analysis.tools_detected)
    return sorted({skill for skill in skills if skill})


def _experience_relevance(profile: UserProfile, text: str) -> float:
    sources = (profile.experience or []) + (profile.projects or []) + (profile.preferred_roles or [])
    if not sources:
        return 35.0
    tokens = _keyword_set(" ".join(sources))
    if not tokens:
        return 35.0
    overlap = sum(1 for token in tokens if token in text)
    return min(100.0, 25.0 + (75.0 * overlap / max(1, len(tokens))))


def _role_family_fit(profile: UserProfile, job: Job, analysis: JobAnalysis | None) -> float:
    role_text = " ".join([job.title or "", analysis.role_family if analysis else ""]).lower()
    roles = [_norm(role) for role in (profile.preferred_roles or [])]
    if not roles:
        return 50.0
    if any(role in _norm(role_text) or _norm(role_text) in role for role in roles):
        return 100.0
    if any(any(part in role_text for part in role.split()) for role in roles):
        return 70.0
    return 25.0


def _location_remote_fit(profile: UserProfile, job: Job) -> float:
    preferred_remote = (profile.remote_preference or (profile.preferences or {}).get("remote") or "").lower()
    preferred_location = (profile.location_preference or (profile.preferences or {}).get("location") or "").lower()
    job_remote = (job.remote_type or "").lower()
    job_location = (job.location or "").lower()
    if preferred_remote and preferred_remote in job_remote:
        return 100.0
    if preferred_remote == "remote" and "onsite" in job_remote:
        return 0.0
    if preferred_remote == "hybrid" and ("hybrid" in job_remote or "remote" in job_remote):
        return 90.0
    if preferred_location and any(part.strip() and part.strip() in job_location for part in preferred_location.split("/")):
        return 90.0
    if not preferred_remote and not preferred_location:
        return 60.0
    return 35.0


def _salary_fit(profile: UserProfile, job: Job) -> tuple[float, list[str]]:
    desired_min = profile.salary_min_preference
    if desired_min is None:
        return (60.0 if job.normalized_annual_min is None and job.normalized_annual_max is None else 80.0), []
    if job.normalized_annual_min is None and job.normalized_annual_max is None:
        return 45.0, ["Salary preference exists, but job salary is not listed."]
    job_max = job.normalized_annual_max or job.normalized_annual_min
    if job_max is not None and job_max < desired_min:
        return 20.0, ["Listed salary appears below saved profile preference."]
    return 100.0, []


def _confidence(job: Job, analysis: JobAnalysis | None, skills: list[str]) -> float:
    score = 20.0
    if job.description_text and len(job.description_text) > 120:
        score += 30.0
    if analysis is not None:
        score += 25.0
    if skills:
        score += 25.0
    return min(100.0, score)


def _evidence(job: Job, analysis: JobAnalysis | None, matched: list[str], profile: UserProfile) -> list[str]:
    evidence = []
    if matched:
        evidence.append(f"Matched skills: {', '.join(matched[:8])}.")
    if analysis and analysis.role_family:
        evidence.append(f"Analysed role family: {analysis.role_family}.")
    if profile.preferred_roles:
        evidence.append(f"Profile preferred roles: {', '.join(profile.preferred_roles[:5])}.")
    if job.remote_type or job.location:
        evidence.append(f"Job location/remote: {job.location or 'not listed'} / {job.remote_type or 'not listed'}.")
    return evidence


def _scorecard_payload(job: Job, result: ScoreResult) -> dict[str, Any]:
    return {
        "job_id": job.id,
        "total_score": result.total_score,
        "tier": result.tier,
        "recommendation": result.recommendation,
        "confidence_score": result.confidence_score,
        "score_breakdown": result.breakdown,
        "matched_skills": result.matched_skills,
        "missing_skills": result.missing_skills,
        "matched_evidence": result.evidence,
        "risks": result.risks,
        "gates": result.gates,
        "why": _why(result),
    }


def _why(result: ScoreResult) -> str:
    if result.gates:
        return "Recommendation is skip because deterministic gates found blocking risks before weighted scoring."
    if result.recommendation == "apply":
        return "Recommendation is apply because weighted score and evidence indicate a strong fit."
    if result.recommendation == "maybe":
        return "Recommendation is maybe because there is partial fit with notable gaps or data limits."
    return "Recommendation is skip because the weighted score is weak."


def _recommendation(total: float, gates: list[str]) -> str:
    if gates or total < 50:
        return "skip"
    if total >= 70:
        return "apply"
    return "maybe"


def _refresh_missing_skills(db: Session, job: Job, user: User, missing_skills: list[str]) -> None:
    db.query(MissingSkill).filter(MissingSkill.job_id == job.id, MissingSkill.user_id == user.id).delete()
    for skill in missing_skills:
        db.add(MissingSkill(job_id=job.id, user_id=user.id, skill_name=skill, learning_priority="medium"))


def _default_user(db: Session) -> User:
    user = db.scalar(select(User).order_by(User.id))
    if user is None:
        raise ValueError("No user exists to score against.")
    return user


def _job_text(job: Job, analysis: JobAnalysis | None) -> str:
    parts = [
        job.title,
        job.company_name,
        job.location,
        job.remote_type,
        job.description_text,
        analysis.role_family if analysis else None,
        analysis.role_focus if analysis else None,
        " ".join(analysis.requirements or []) if analysis else None,
        " ".join(analysis.responsibilities or []) if analysis else None,
    ]
    return " ".join(part for part in parts if part).lower()


def _keyword_set(value: str) -> set[str]:
    stop = {"and", "the", "with", "for", "using", "built", "role", "roles", "engineer", "developer"}
    return {token for token in re.findall(r"[a-zA-Z][a-zA-Z+#.]{2,}", value.lower()) if token not in stop}


def _norm(value: str) -> str:
    return value.strip().lower().replace("-", " ").replace("_", " ")


def _decimal(value: float) -> Decimal:
    return Decimal(str(round(value, 2)))
