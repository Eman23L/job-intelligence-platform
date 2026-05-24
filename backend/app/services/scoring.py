from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Job, JobAnalysis, JobScore, JobSkill, MissingSkill, TargetRole, User, UserSkill

ROLE_WEIGHT = 25
SKILL_WEIGHT = 30
EXPERIENCE_WEIGHT = 10
TOOLING_WEIGHT = 10
LOCATION_WEIGHT = 10
SALARY_WEIGHT = 5
FRESHNESS_WEIGHT = 5
QUALITY_WEIGHT = 5


@dataclass(frozen=True)
class SkillMatchResult:
    matched_skills: list[str]
    missing_skills: list[JobSkill]
    critical_missing: list[JobSkill]
    important_missing: list[JobSkill]
    nice_to_have_missing: list[JobSkill]
    score: float


@dataclass(frozen=True)
class ScoreBreakdown:
    role_match_score: float
    skill_match_score: float
    experience_score: float
    tooling_match_score: float
    location_score: float
    salary_score: float
    freshness_score: float
    quality_signal_score: float
    penalty: float
    excluded_essential: bool
    warnings: list[str]
    skill_match: SkillMatchResult


def score_job(db: Session, job: Job, user: User | None = None) -> JobScore:
    user = user or _default_user(db)
    analysis = _analysis_for_job(db, job)
    job_skills = db.scalars(select(JobSkill).where(JobSkill.job_id == job.id)).all()
    user_skills = db.scalars(select(UserSkill).where(UserSkill.user_id == user.id)).all()
    target_roles = db.scalars(select(TargetRole).where(TargetRole.user_id == user.id)).all()

    breakdown = build_score_breakdown(job, analysis, job_skills, user_skills, target_roles)
    total = (
        breakdown.role_match_score
        + breakdown.skill_match_score
        + breakdown.experience_score
        + breakdown.tooling_match_score
        + breakdown.location_score
        + breakdown.salary_score
        + breakdown.freshness_score
        + breakdown.quality_signal_score
        - breakdown.penalty
    )
    total = max(0.0, min(100.0, total))
    if breakdown.excluded_essential:
        total = min(total, 25.0)

    tier = "excluded" if breakdown.excluded_essential else recommendation_tier(total)
    explanation = generate_explanation(job, analysis, breakdown, tier)

    score = db.scalar(select(JobScore).where(JobScore.job_id == job.id, JobScore.user_id == user.id))
    if score is None:
        score = JobScore(job_id=job.id, user_id=user.id, total_score=Decimal("0"))
        db.add(score)

    score.total_score = _decimal(total)
    score.role_match_score = _decimal(breakdown.role_match_score)
    score.skill_match_score = _decimal(breakdown.skill_match_score)
    score.experience_score = _decimal(breakdown.experience_score)
    score.salary_score = _decimal(breakdown.salary_score)
    score.location_score = _decimal(breakdown.location_score)
    score.freshness_score = _decimal(breakdown.freshness_score)
    score.missing_skill_penalty = _decimal(breakdown.penalty)
    score.explanation = explanation
    score.recommendation = _recommendation_action(total, tier)
    score.recommendation_tier = tier
    score.scored_at = datetime.now(tz=timezone.utc)

    _refresh_missing_skills(db, job, user, breakdown.skill_match)
    db.commit()
    db.refresh(score)
    return score


def score_all_jobs(db: Session, user: User | None = None) -> dict[str, int]:
    user = user or _default_user(db)
    jobs = db.scalars(select(Job).order_by(Job.id)).all()
    scored = 0
    skipped = 0
    for job in jobs:
        if db.scalar(select(JobAnalysis).where(JobAnalysis.job_id == job.id)) is None:
            skipped += 1
            continue
        score_job(db, job, user)
        scored += 1
    return {"jobs_scored": scored, "jobs_skipped": skipped}


def build_score_breakdown(
    job: Job,
    analysis: JobAnalysis,
    job_skills: list[JobSkill],
    user_skills: list[UserSkill],
    target_roles: list[TargetRole],
) -> ScoreBreakdown:
    skill_match = score_skill_match(job_skills, user_skills)
    warnings, excluded_essential, penalty = excluded_technology_penalty(analysis.red_flags or [])
    return ScoreBreakdown(
        role_match_score=score_role_match(analysis.role_family, target_roles),
        skill_match_score=skill_match.score,
        experience_score=score_experience(analysis.seniority_level),
        tooling_match_score=score_tooling_match(analysis.tools_detected or [], user_skills),
        location_score=score_location(job),
        salary_score=score_salary(job),
        freshness_score=score_freshness(job),
        quality_signal_score=score_quality_signal(job, analysis),
        penalty=penalty,
        excluded_essential=excluded_essential,
        warnings=warnings,
        skill_match=skill_match,
    )


def score_role_match(role_family: str | None, target_roles: list[TargetRole]) -> float:
    if not role_family:
        return 5.0
    targets = {role.role_title.lower() for role in target_roles}
    role = role_family.lower()
    if role in targets:
        return float(ROLE_WEIGHT)
    if "software engineer" in role:
        return 15.0
    similar_groups = [
        {"data engineer", "python data engineer", "data platform engineer"},
        {"analytics engineer", "data & automation engineer"},
        {"workflow automation engineer", "process automation engineer", "full stack automation engineer"},
        {"internal tools engineer", "full stack automation engineer"},
        {"ai automation engineer", "data & automation engineer"},
        {"technical consultant data automation", "digital transformation consultant"},
    ]
    for group in similar_groups:
        if role in group and targets.intersection(group):
            return 18.0
    return 5.0


def score_skill_match(job_skills: list[JobSkill], user_skills: list[UserSkill]) -> SkillMatchResult:
    user_skill_names = {_normalise_skill(skill.skill_name) for skill in user_skills}
    matched: list[str] = []
    missing: list[JobSkill] = []
    critical: list[JobSkill] = []
    important: list[JobSkill] = []
    nice: list[JobSkill] = []
    matched_weight = 0.0
    total_weight = 0.0

    for skill in job_skills:
        weight = _skill_weight(skill.importance)
        total_weight += weight
        if _normalise_skill(skill.skill_name) in user_skill_names:
            matched.append(skill.skill_name)
            matched_weight += weight
        else:
            missing.append(skill)
            if skill.importance == "essential":
                critical.append(skill)
            elif skill.importance == "nice_to_have":
                nice.append(skill)
            else:
                important.append(skill)

    score = SKILL_WEIGHT if total_weight == 0 else SKILL_WEIGHT * (matched_weight / total_weight)
    return SkillMatchResult(
        matched_skills=sorted(set(matched)),
        missing_skills=missing,
        critical_missing=critical,
        important_missing=important,
        nice_to_have_missing=nice,
        score=score,
    )


def score_experience(seniority_level: str | None) -> float:
    if seniority_level in {None, "mid"}:
        return 8.0
    if seniority_level == "junior":
        return 10.0
    if seniority_level == "senior":
        return 7.0
    if seniority_level == "lead":
        return 4.0
    return 6.0


def score_tooling_match(tools_detected: list[str], user_skills: list[UserSkill]) -> float:
    if not tools_detected:
        return 6.0
    user_skill_names = {_normalise_skill(skill.skill_name) for skill in user_skills}
    matched = sum(1 for tool in set(tools_detected) if _normalise_skill(tool) in user_skill_names)
    return TOOLING_WEIGHT * (matched / len(set(tools_detected)))


def score_location(job: Job) -> float:
    text = " ".join(part for part in [job.remote_type, job.location] if part).lower()
    if "remote" in text or "hybrid" in text:
        return 10.0
    if "uk" in text or "london" in text:
        return 8.0
    if not text:
        return 6.0
    return 4.0


def score_salary(job: Job) -> float:
    if job.salary_min is None and job.salary_max is None:
        return 3.0
    return 5.0


def score_freshness(job: Job) -> float:
    if job.posted_at is None:
        return 2.0
    posted = job.posted_at
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=timezone.utc)
    age_days = (datetime.now(tz=timezone.utc) - posted).days
    if age_days <= 7:
        return 5.0
    if age_days <= 30:
        return 3.0
    return 1.0


def score_quality_signal(job: Job, analysis: JobAnalysis) -> float:
    description = job.description_text or ""
    if len(description) >= 150 and analysis.responsibilities and analysis.requirements:
        return 5.0
    if len(description) >= 80:
        return 3.0
    return 1.0


def excluded_technology_penalty(red_flags: list[str]) -> tuple[list[str], bool, float]:
    warnings: list[str] = []
    excluded_essential = False
    penalty = 0.0
    for flag in red_flags:
        lowered = flag.lower()
        if "essential requirement" in lowered:
            excluded_essential = True
            warnings.append(flag)
        elif "nice-to-have" in lowered:
            penalty += 8.0
            warnings.append(flag)
        elif "minor mention" in lowered:
            warnings.append(flag)
    return warnings, excluded_essential, penalty


def recommendation_tier(total_score: float) -> str:
    if total_score >= 85:
        return "Excellent match"
    if total_score >= 70:
        return "Strong match"
    if total_score >= 55:
        return "Possible match"
    if total_score >= 40:
        return "Stretch role"
    return "Poor fit"


def _recommendation_action(total_score: float, tier: str) -> str:
    if tier == "excluded" or total_score < 50:
        return "skip"
    if total_score >= 70:
        return "apply"
    return "maybe"


def generate_explanation(job: Job, analysis: JobAnalysis, breakdown: ScoreBreakdown, tier: str) -> str:
    matched = _join_names(breakdown.skill_match.matched_skills[:4])
    missing = _join_names([skill.skill_name for skill in breakdown.skill_match.missing_skills[:4]])
    role = analysis.role_family or "the analysed role"
    phrase = f"This is a {tier.lower()} because it aligns with {role} roles"
    if matched:
        phrase += f", matches {matched}"
    if missing:
        phrase += f", but requires {missing} which are currently missing"
    if breakdown.excluded_essential:
        phrase += ", and includes excluded technology as an essential requirement"
    elif breakdown.warnings:
        phrase += ", with excluded technology warnings"
    return phrase + "."


def top_scores(db: Session, limit: int = 10) -> list[JobScore]:
    return db.scalars(select(JobScore).order_by(JobScore.total_score.desc()).limit(limit)).all()


def missing_skills_summary(db: Session) -> list[dict[str, str | int]]:
    rows = db.scalars(select(MissingSkill)).all()
    summary: dict[str, dict[str, str | int]] = {}
    priority_rank = {"high": 3, "medium": 2, "low": 1}
    for row in rows:
        current = summary.setdefault(
            row.skill_name,
            {"skill_name": row.skill_name, "count": 0, "highest_priority": row.learning_priority or "low"},
        )
        current["count"] = int(current["count"]) + 1
        if priority_rank.get(row.learning_priority or "low", 1) > priority_rank.get(str(current["highest_priority"]), 1):
            current["highest_priority"] = row.learning_priority or "low"
    return sorted(summary.values(), key=lambda item: (-int(item["count"]), str(item["skill_name"])))


def recommendations(db: Session, limit: int = 20) -> list[JobScore]:
    return db.scalars(
        select(JobScore)
        .where(JobScore.recommendation_tier != "excluded")
        .order_by(JobScore.total_score.desc())
        .limit(limit)
    ).all()


def _refresh_missing_skills(db: Session, job: Job, user: User, skill_match: SkillMatchResult) -> None:
    db.query(MissingSkill).filter(MissingSkill.job_id == job.id, MissingSkill.user_id == user.id).delete()
    for skill in skill_match.missing_skills:
        db.add(
            MissingSkill(
                job_id=job.id,
                user_id=user.id,
                skill_name=skill.skill_name,
                importance=skill.importance,
                learning_priority=_learning_priority(skill.importance),
                evidence_text=skill.evidence_text,
            )
        )


def _learning_priority(importance: str | None) -> str:
    if importance == "essential":
        return "high"
    if importance == "nice_to_have":
        return "low"
    return "medium"


def _default_user(db: Session) -> User:
    user = db.scalar(select(User).order_by(User.id))
    if user is None:
        raise ValueError("No user exists to score against. Run seed data first.")
    return user


def _analysis_for_job(db: Session, job: Job) -> JobAnalysis:
    analysis = db.scalar(select(JobAnalysis).where(JobAnalysis.job_id == job.id))
    if analysis is None:
        raise ValueError("Job must be analysed before scoring.")
    return analysis


def _skill_weight(importance: str | None) -> float:
    if importance == "essential":
        return 3.0
    if importance == "nice_to_have":
        return 1.0
    return 2.0


def _normalise_skill(value: str) -> str:
    return value.strip().lower().replace("-", " ").replace("_", " ")


def _decimal(value: float) -> Decimal:
    return Decimal(str(round(value, 2)))


def _join_names(names: list[str]) -> str:
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + f" and {names[-1]}"
