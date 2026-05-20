from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.services.ai_provider import AIProviderError, get_ai_provider

router = APIRouter(prefix="/ai", tags=["ai"])


class AITestRequest(BaseModel):
    message: str = Field(min_length=1)


class AITestResponse(BaseModel):
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
