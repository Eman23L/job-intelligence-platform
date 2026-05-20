from collections import Counter, defaultdict
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.models import Job, JobAnalysis, JobScore, JobSkill, JobSource, MissingSkill, SavedJob, ScrapeRun
from app.schemas.database import (
    AnalyticsOverview,
    RoleFitAnalytics,
    RoleFitItem,
    SalaryAnalytics,
    SalaryGroup,
    SkillGapAnalytics,
    SkillGapItem,
    SourceHealthAnalytics,
    SourceHealthItem,
)


def overview(db: Session) -> AnalyticsOverview:
    try:
        scores = db.scalars(select(JobScore).join(Job).where(_included_job_filter())).all()
        score_values = [score.total_score for score in scores if score.total_score is not None]
        return AnalyticsOverview(
            total_jobs=db.scalar(select(func.count(Job.id)).where(Job.status != "excluded")) or 0,
            analysed_jobs=(
                db.scalar(select(func.count(JobAnalysis.id)).join(Job).where(_included_job_filter())) or 0
            ),
            scored_jobs=len(scores),
            saved_jobs=(
                db.scalar(select(func.count(SavedJob.id)).join(Job).where(Job.status != "excluded", SavedJob.status == "saved"))
                or 0
            ),
            applied_jobs=(
                db.scalar(select(func.count(SavedJob.id)).join(Job).where(Job.status != "excluded", SavedJob.status == "applied"))
                or 0
            ),
            excellent_matches=sum(1 for score in scores if score.recommendation_tier == "Excellent match"),
            strong_matches=sum(1 for score in scores if score.recommendation_tier == "Strong match"),
            stretch_roles=sum(1 for score in scores if score.recommendation_tier == "Stretch role"),
            excluded_jobs=(
                db.scalar(
                    select(func.count(func.distinct(Job.id)))
                    .outerjoin(JobScore)
                    .where(or_(Job.status == "excluded", JobScore.recommendation_tier == "excluded"))
                )
                or 0
            ),
            average_score=_avg(score_values) or Decimal("0"),
            newest_job_date=db.scalar(select(func.max(Job.posted_at)).where(Job.status != "excluded")),
        )
    except SQLAlchemyError:
        return _empty_overview()


def _empty_overview() -> AnalyticsOverview:
    return AnalyticsOverview(
        total_jobs=0,
        analysed_jobs=0,
        scored_jobs=0,
        saved_jobs=0,
        applied_jobs=0,
        excellent_matches=0,
        strong_matches=0,
        stretch_roles=0,
        excluded_jobs=0,
        average_score=Decimal("0"),
        newest_job_date=None,
    )


def role_fit(db: Session) -> RoleFitAnalytics:
    analyses = db.scalars(select(JobAnalysis).join(Job).where(_included_job_filter())).all()
    grouped: dict[str | None, list[JobAnalysis]] = defaultdict(list)
    for analysis in analyses:
        grouped[analysis.role_family].append(analysis)

    items = []
    for role_family, entries in grouped.items():
        scores = []
        tiers: Counter[str] = Counter()
        for analysis in entries:
            score = db.scalar(select(JobScore).where(JobScore.job_id == analysis.job_id))
            if score is not None:
                scores.append(score.total_score)
                if score.recommendation_tier:
                    tiers[score.recommendation_tier] += 1
        items.append(
            RoleFitItem(
                role_family=role_family,
                count=len(entries),
                average_score=_avg(scores),
                recommendation_tiers=dict(tiers),
            )
        )
    return RoleFitAnalytics(items=sorted(items, key=lambda item: item.count, reverse=True))


def skill_gaps(db: Session) -> SkillGapAnalytics:
    missing_rows = db.scalars(select(MissingSkill).join(Job).where(_included_job_filter())).all()
    missing_items = _missing_summary(missing_rows)
    skill_counts = Counter(db.scalars(select(JobSkill.skill_name).join(Job).where(_included_job_filter())).all())
    linked = [
        SkillGapItem(skill_name=name, count=count, highest_priority=None)
        for name, count in skill_counts.most_common()
    ]
    high = [item for item in missing_items if item.highest_priority == "high"]
    priority_rank = {"high": 3, "medium": 2, "low": 1, None: 0}
    top_priorities = sorted(
        missing_items,
        key=lambda item: (-priority_rank[item.highest_priority], -item.count, item.skill_name),
    )[:10]
    return SkillGapAnalytics(
        missing_skill_frequency=missing_items,
        high_priority_missing_skills=high,
        skills_linked_to_most_jobs=linked,
        top_10_learning_priorities=top_priorities,
    )


def salary(db: Session) -> SalaryAnalytics:
    jobs = db.scalars(select(Job).where(Job.status != "excluded")).all()
    jobs_with_salary = [job for job in jobs if job.normalized_annual_min is not None or job.normalized_annual_max is not None]
    return SalaryAnalytics(
        average_salary_min=_avg([job.normalized_annual_min for job in jobs if job.normalized_annual_min is not None]),
        average_salary_max=_avg([job.normalized_annual_max for job in jobs if job.normalized_annual_max is not None]),
        salary_by_role_family=_salary_by_role_family(db),
        salary_by_remote_type=_salary_group(jobs_with_salary, lambda job: job.remote_type),
        missing_salary_count=sum(1 for job in jobs if job.normalized_annual_min is None and job.normalized_annual_max is None),
    )


def source_health(db: Session) -> SourceHealthAnalytics:
    items = []
    for source in db.scalars(select(JobSource).order_by(JobSource.name)).all():
        run = db.scalar(
            select(ScrapeRun).where(ScrapeRun.source_id == source.id).order_by(ScrapeRun.started_at.desc())
        )
        items.append(
            SourceHealthItem(
                source_id=source.id,
                source_name=source.name,
                jobs_count=(
                    db.scalar(select(func.count(Job.id)).where(Job.source_id == source.id, Job.status != "excluded")) or 0
                ),
                last_scrape_run_id=run.id if run else None,
                last_scrape_started_at=run.started_at if run else None,
                last_scrape_finished_at=run.finished_at if run else None,
                scrape_status=run.status if run else None,
                jobs_found=run.jobs_found if run else None,
                jobs_created=run.jobs_created if run else None,
                jobs_updated=run.jobs_updated if run else None,
                error_message=run.error_message if run else None,
            )
        )
    return SourceHealthAnalytics(items=items)


def _missing_summary(rows: list[MissingSkill]) -> list[SkillGapItem]:
    priority_rank = {"high": 3, "medium": 2, "low": 1}
    grouped: dict[str, dict[str, int | str | None]] = {}
    for row in rows:
        current = grouped.setdefault(
            row.skill_name,
            {"count": 0, "highest_priority": row.learning_priority},
        )
        current["count"] = int(current["count"]) + 1
        if priority_rank.get(row.learning_priority or "low", 1) > priority_rank.get(
            str(current["highest_priority"] or "low"), 1
        ):
            current["highest_priority"] = row.learning_priority
    return [
        SkillGapItem(skill_name=name, count=int(data["count"]), highest_priority=data["highest_priority"])
        for name, data in sorted(grouped.items(), key=lambda item: (-int(item[1]["count"]), item[0]))
    ]


def _salary_by_role_family(db: Session) -> list[SalaryGroup]:
    analyses = db.scalars(select(JobAnalysis).join(Job).where(_included_job_filter())).all()
    grouped: dict[str | None, list[Job]] = defaultdict(list)
    for analysis in analyses:
        job = db.get(Job, analysis.job_id)
        if job and (job.normalized_annual_min is not None or job.normalized_annual_max is not None):
            grouped[analysis.role_family].append(job)
    return [
        SalaryGroup(
            group=group,
            average_salary_min=_avg([job.normalized_annual_min for job in jobs if job.normalized_annual_min is not None]),
            average_salary_max=_avg([job.normalized_annual_max for job in jobs if job.normalized_annual_max is not None]),
            count=len(jobs),
        )
        for group, jobs in sorted(grouped.items(), key=lambda item: str(item[0]))
    ]


def _salary_group(jobs: list[Job], key_fn) -> list[SalaryGroup]:
    grouped: dict[str | None, list[Job]] = defaultdict(list)
    for job in jobs:
        grouped[key_fn(job)].append(job)
    return [
        SalaryGroup(
            group=group,
            average_salary_min=_avg([job.normalized_annual_min for job in entries if job.normalized_annual_min is not None]),
            average_salary_max=_avg([job.normalized_annual_max for job in entries if job.normalized_annual_max is not None]),
            count=len(entries),
        )
        for group, entries in sorted(grouped.items(), key=lambda item: str(item[0]))
    ]


def _avg(values: list[Decimal | None]) -> Decimal | None:
    clean = [value for value in values if value is not None]
    if not clean:
        return None
    return Decimal(str(round(sum(clean) / len(clean), 2)))


def _included_job_filter():
    return Job.status != "excluded"
