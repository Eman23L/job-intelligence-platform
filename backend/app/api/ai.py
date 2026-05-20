from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.db.models import Job, JobAnalysis, JobScore, User
from app.db.session import get_db
from app.services.ai_provider import AIProviderError, get_ai_provider
from app.services.profile import get_profile

router = APIRouter(prefix="/ai", tags=["ai"])


class AITestRequest(BaseModel):
    message: str = Field(min_length=1)


class AITestResponse(BaseModel):
    provider: str
    model: str
    response: str


class AIChatRequest(BaseModel):
    message: str = Field(min_length=1)


class AIChatResponse(BaseModel):
    provider: str
    model: str
    response: str


@router.post("/test", response_model=AITestResponse)
def test_ai_provider(payload: AITestRequest):
    provider = get_ai_provider()
    try:
        response = provider.send_chat([{"role": "user", "content": payload.message}])
    except AIProviderError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    return AITestResponse(provider=provider.provider_name, model=provider.model_name, response=response)


@router.post("/chat", response_model=AIChatResponse)
def chat_with_advisor(payload: AIChatRequest, db: Session = Depends(get_db)):
    provider = get_ai_provider()
    messages = [
        {
            "role": "system",
            "content": (
                "You are an AI career advisor for a job search dashboard. Use only the provided CV/profile "
                "and scraped jobs context. Do not invent experience, credentials, employers, or projects. "
                "Say clearly when profile or job data is missing. This is advisory only; do not create or imply "
                "a final scoring system."
            ),
        },
        {
            "role": "user",
            "content": _advisor_prompt(db, payload.message),
        },
    ]
    try:
        response = provider.send_chat(messages)
    except AIProviderError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    return AIChatResponse(provider=provider.provider_name, model=provider.model_name, response=response)


def _advisor_prompt(db: Session, question: str) -> str:
    user = db.scalar(select(User).order_by(User.id))
    profile = get_profile(db, user) if user is not None else None
    return (
        "CV/Profile context:\n"
        f"{_profile_context(profile)}\n\n"
        "Top scraped jobs context:\n"
        f"{_jobs_context(db)}\n\n"
        f"User question:\n{question}"
    )


def _profile_context(profile) -> str:
    if profile is None:
        return "No saved CV/profile is available."
    lines = [
        f"Summary: {profile.summary or 'Not provided'}",
        f"Skills: {', '.join(profile.skills or []) or 'Not provided'}",
        f"Preferred roles: {', '.join(profile.preferred_roles or []) or 'Not provided'}",
        f"Preferences: {profile.preferences or {}}",
        f"Projects: {' | '.join((profile.projects or [])[:8]) or 'Not provided'}",
        f"Experience: {' | '.join((profile.experience or [])[:8]) or 'Not provided'}",
        f"Education: {' | '.join(profile.education or []) or 'Not provided'}",
    ]
    return "\n".join(lines)


def _jobs_context(db: Session) -> str:
    rows = db.execute(
        select(Job, JobAnalysis, JobScore)
        .outerjoin(JobAnalysis, JobAnalysis.job_id == Job.id)
        .outerjoin(JobScore, JobScore.job_id == Job.id)
        .where(Job.status != "excluded")
        .order_by(desc(Job.posted_at), desc(Job.id))
        .limit(10)
    ).all()
    if not rows:
        return "No scraped jobs are available."
    items = []
    for index, (job, analysis, score) in enumerate(rows, start=1):
        salary = _salary_text(job.normalized_annual_min, job.normalized_annual_max, job.salary_currency)
        items.append(
            (
                f"{index}. {job.title} at {job.company_name or 'Unknown company'}; "
                f"location={job.location or 'Not listed'}; remote={job.remote_type or 'Not listed'}; "
                f"salary={salary}; role_family={analysis.role_family if analysis else 'Not analysed'}; "
                f"tier={score.recommendation_tier if score else 'Not scored'}"
            )
        )
    return "\n".join(items)


def _salary_text(min_value: Decimal | None, max_value: Decimal | None, currency: str | None) -> str:
    if min_value is None and max_value is None:
        return "Not listed"
    prefix = currency or "GBP"
    if min_value is not None and max_value is not None:
        return f"{prefix} {int(min_value)}-{int(max_value)}"
    return f"{prefix} {int(min_value or max_value or 0)}"
