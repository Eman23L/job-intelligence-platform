import httpx
from fastapi.testclient import TestClient

from app.main import app
from app.services.ai_provider import AIProviderError, GroqProvider


class FakeClient:
    calls = 0
    statuses: list[int] = [200]

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def post(self, url, *, headers, json):
        FakeClient.calls += 1
        status = FakeClient.statuses[min(FakeClient.calls - 1, len(FakeClient.statuses) - 1)]
        payload = {"choices": [{"message": {"content": "AI provider is ready"}}]}
        return httpx.Response(status, json=payload, request=httpx.Request("POST", url))


def test_groq_provider_sends_openai_compatible_chat_request() -> None:
    FakeClient.calls = 0
    FakeClient.statuses = [200]
    provider = GroqProvider(
        api_key="test-key",
        model_name="llama-3.1-8b-instant",
        client_factory=FakeClient,
        sleep=lambda _: None,
    )

    response = provider.send_chat([{"role": "user", "content": "hello"}])

    assert response == "AI provider is ready"
    assert provider.provider_name == "groq"
    assert provider.model_name == "llama-3.1-8b-instant"
    assert FakeClient.calls == 1


def test_groq_provider_retries_retryable_status() -> None:
    FakeClient.calls = 0
    FakeClient.statuses = [429, 200]
    provider = GroqProvider(
        api_key="test-key",
        model_name="llama-3.1-8b-instant",
        max_retries=1,
        client_factory=FakeClient,
        sleep=lambda _: None,
    )

    assert provider.send_chat([{"role": "user", "content": "hello"}]) == "AI provider is ready"
    assert FakeClient.calls == 2


def test_groq_provider_requires_api_key() -> None:
    provider = GroqProvider(api_key="", model_name="llama-3.1-8b-instant", client_factory=FakeClient)

    try:
        provider.send_chat([{"role": "user", "content": "hello"}])
    except AIProviderError as exc:
        assert "GROQ_API_KEY" in str(exc)
    else:
        raise AssertionError("Expected AIProviderError")


def test_ai_test_endpoint_returns_provider_model_and_response(monkeypatch) -> None:
    class FakeProvider:
        provider_name = "groq"
        model_name = "llama-3.1-8b-instant"

        def send_chat(self, messages):
            assert messages == [{"role": "user", "content": "ping"}]
            return "pong"

    monkeypatch.setattr("app.api.ai.get_ai_provider", lambda: FakeProvider())

    response = TestClient(app).post("/ai/test", json={"message": "ping"})

    assert response.status_code == 200
    assert response.json() == {
        "provider": "groq",
        "model": "llama-3.1-8b-instant",
        "response": "pong",
    }
