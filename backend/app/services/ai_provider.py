from collections.abc import Callable
import time
from typing import Any

import httpx

from app.config import Settings, settings


GROQ_CHAT_COMPLETIONS_URL = "https://api.groq.com/openai/v1/chat/completions"
RETRYABLE_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}


class AIProviderError(RuntimeError):
    pass


class AIProvider:
    provider_name: str
    model_name: str

    def send_chat(self, messages: list[dict[str, str]]) -> str:
        raise NotImplementedError


class GroqProvider(AIProvider):
    provider_name = "groq"

    def __init__(
        self,
        *,
        api_key: str,
        model_name: str,
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
        client_factory: Callable[..., httpx.Client] = httpx.Client,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.api_key = api_key
        self.model_name = model_name
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(0, max_retries)
        self._client_factory = client_factory
        self._sleep = sleep

    def send_chat(self, messages: list[dict[str, str]]) -> str:
        if not self.api_key.strip():
            raise AIProviderError("GROQ_API_KEY is not configured")
        if not messages:
            raise AIProviderError("At least one message is required")

        payload = {"model": self.model_name, "messages": messages}
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            try:
                with self._client_factory(timeout=self.timeout_seconds) as client:
                    response = client.post(GROQ_CHAT_COMPLETIONS_URL, headers=headers, json=payload)
                if response.status_code in RETRYABLE_STATUS_CODES and attempt < self.max_retries:
                    self._sleep(_retry_delay(attempt))
                    continue
                response.raise_for_status()
                return _extract_chat_response(response.json())
            except (httpx.RequestError, httpx.HTTPStatusError, AIProviderError) as exc:
                last_error = exc
                if attempt >= self.max_retries or isinstance(exc, AIProviderError):
                    break
                self._sleep(_retry_delay(attempt))

        raise AIProviderError(f"AI provider request failed: {last_error}") from last_error


def get_ai_provider(config: Settings = settings) -> AIProvider:
    provider = config.ai_provider.lower().strip()
    if provider == "groq":
        return GroqProvider(
            api_key=config.groq_api_key,
            model_name=config.ai_model,
            timeout_seconds=config.ai_timeout_seconds,
            max_retries=config.ai_max_retries,
        )
    raise AIProviderError(f"Unsupported AI provider: {config.ai_provider}")


def _extract_chat_response(payload: dict[str, Any]) -> str:
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AIProviderError("AI provider response did not include chat content") from exc
    if not isinstance(content, str) or not content.strip():
        raise AIProviderError("AI provider response content was empty")
    return content


def _retry_delay(attempt: int) -> float:
    return min(0.25 * (2**attempt), 2.0)
