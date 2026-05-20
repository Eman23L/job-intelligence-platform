import json
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.db.models import AIConversationMessage, Job, JobAnalysis, JobScore, User
from app.db.session import get_db
from app.services.ai_provider import AIProviderError, get_ai_provider
from app.services.profile import get_profile
from app.services.profile_context import build_profile_context

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


class AIHistoryMessage(BaseModel):
    id: int
    role: str
    content: str
    metadata: dict[str, Any] | None = None
    created_at: str


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
    user = _default_user(db)
    user_message = _save_message(db, user, "user", payload.message, {"source": "ai_advisor"})
    db.flush()
    provider = get_ai_provider()
    messages = [
        {
            "role": "system",
            "content": _system_prompt(),
        },
        {
            "role": "user",
            "content": _advisor_context_prompt(db, user),
        },
    ]
    messages.extend(_conversation_memory(db, user, latest_user_message_id=user_message.id))
    try:
        response = provider.send_chat(messages)
    except AIProviderError as exc:
        db.commit()
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    _save_message(db, user, "assistant", response, {"provider": provider.provider_name, "model": provider.model_name})
    db.commit()
    return AIChatResponse(provider=provider.provider_name, model=provider.model_name, response=response)


@router.get("/history", response_model=list[AIHistoryMessage])
def get_ai_history(limit: int = 50, db: Session = Depends(get_db)):
    user = _default_user(db)
    messages = db.scalars(
        select(AIConversationMessage)
        .where(AIConversationMessage.user_id == user.id)
        .order_by(desc(AIConversationMessage.created_at), desc(AIConversationMessage.id))
        .limit(min(max(limit, 1), 100))
    ).all()
    return [_history_message(message) for message in reversed(messages)]


@router.delete("/history")
def clear_ai_history(db: Session = Depends(get_db)):
    user = _default_user(db)
    deleted = (
        db.query(AIConversationMessage)
        .filter(AIConversationMessage.user_id == user.id)
        .delete(synchronize_session=False)
    )
    db.commit()
    return {"deleted": deleted}


def _advisor_context_prompt(db: Session, user: User) -> str:
    profile = get_profile(db, user)
    context = {
        "profile": build_profile_context(profile),
        "jobs": _jobs_context(db, user),
    }
    return (
        "Structured advisor context follows as compact JSON. Use this context and the conversation memory. "
        "Raw CV text is intentionally omitted.\n"
        f"{json.dumps(context, ensure_ascii=False, indent=2)}"
    )


def _jobs_context(db: Session, user: User) -> list[dict[str, Any]]:
    scored_rows = db.execute(
        select(Job, JobAnalysis, JobScore)
        .join(JobScore, JobScore.job_id == Job.id)
        .outerjoin(JobAnalysis, JobAnalysis.job_id == Job.id)
        .where(Job.status != "excluded", JobScore.user_id == user.id)
        .order_by(desc(JobScore.total_score), desc(JobScore.scored_at), desc(Job.id))
        .limit(10)
    ).all()
    rows = scored_rows
    if not rows:
        rows = db.execute(
            select(Job, JobAnalysis, JobScore)
            .outerjoin(JobAnalysis, JobAnalysis.job_id == Job.id)
            .outerjoin(JobScore, JobScore.job_id == Job.id)
            .where(Job.status != "excluded")
            .order_by(desc(Job.posted_at), desc(Job.id))
            .limit(10)
        ).all()
    if not rows:
        return []
    items = []
    for job, analysis, score in rows:
        scorecard = _scorecard(score)
        item = {
            "title": job.title,
            "company": job.company_name or "",
            "location": job.location or "",
            "remote": job.remote_type or "",
            "salary": _salary_text(job.normalized_annual_min, job.normalized_annual_max, job.salary_currency),
            "role_family": analysis.role_family if analysis else "",
        }
        if score is not None:
            item.update(
                {
                    "score": float(score.total_score),
                    "tier": score.recommendation_tier or "",
                    "recommendation": scorecard.get("recommendation", ""),
                    "matched_skills": scorecard.get("matched_skills", []),
                    "missing_skills": scorecard.get("missing_skills", []),
                    "risks": scorecard.get("risks", []),
                }
            )
        items.append(item)
    return items


def _system_prompt() -> str:
    return (
        "You are an AI career advisor for a job search dashboard. Be concise and useful. "
        "Use only the structured profile, scored jobs, and conversation memory provided. "
        "Do not invent experience, credentials, employers, projects, clearance, or preferences. "
        "Do not repeat the user's name constantly. Do not start every answer with 'Based on the provided CV'. "
        "Give decisive recommendations when evidence is strong. Mention missing data once, not repeatedly. "
        "Use bullets and clear ranking. Use scoring evidence when available. AI explains evidence but does not create scores."
    )


def _conversation_memory(db: Session, user: User, latest_user_message_id: int) -> list[dict[str, str]]:
    rows = db.scalars(
        select(AIConversationMessage)
        .where(AIConversationMessage.user_id == user.id, AIConversationMessage.role.in_(["user", "assistant"]))
        .order_by(desc(AIConversationMessage.created_at), desc(AIConversationMessage.id))
        .limit(12)
    ).all()
    messages = list(reversed(rows))
    if not any(message.id == latest_user_message_id for message in messages):
        latest = db.get(AIConversationMessage, latest_user_message_id)
        if latest is not None:
            messages.append(latest)
    return [{"role": message.role, "content": message.content} for message in messages]


def _save_message(
    db: Session,
    user: User,
    role: str,
    content: str,
    metadata: dict[str, Any] | None = None,
) -> AIConversationMessage:
    message = AIConversationMessage(user_id=user.id, role=role, content=content, metadata_json=metadata)
    db.add(message)
    return message


def _scorecard(score: JobScore | None) -> dict[str, Any]:
    if score is None or not score.explanation:
        return {}
    try:
        payload = json.loads(score.explanation)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _history_message(message: AIConversationMessage) -> AIHistoryMessage:
    return AIHistoryMessage(
        id=message.id,
        role=message.role,
        content=message.content,
        metadata=message.metadata_json,
        created_at=message.created_at.isoformat(),
    )


def _default_user(db: Session) -> User:
    user = db.scalar(select(User).order_by(User.id))
    if user is None:
        user = User(email="advisor@example.invalid")
        db.add(user)
        db.flush()
    return user


def _salary_text(min_value: Decimal | None, max_value: Decimal | None, currency: str | None) -> str:
    if min_value is None and max_value is None:
        return "Not listed"
    prefix = currency or "GBP"
    if min_value is not None and max_value is not None:
        return f"{prefix} {int(min_value)}-{int(max_value)}"
    return f"{prefix} {int(min_value or max_value or 0)}"
