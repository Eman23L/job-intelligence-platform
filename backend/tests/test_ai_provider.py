import httpx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Job, JobAnalysis, JobSource, User, UserProfile
from app.db.session import get_db
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


def test_ai_chat_endpoint_uses_profile_and_jobs_context(monkeypatch) -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)
    with TestingSession() as db:
        user = User(email="advisor@example.invalid")
        db.add(user)
        db.flush()
        db.add(
            UserProfile(
                user_id=user.id,
                cv_text="CV text",
                summary="Automation engineer with Power BI experience.",
                skills=["Python", "Power BI"],
                experience=["Built reporting automation."],
                projects=["Power BI Timesheet Dashboard"],
                education=["University of Roehampton"],
                preferred_roles=["Automation Engineer"],
                preferences={"remote": "hybrid", "location": "Milton Keynes", "salary": "", "work_authorization": "BPSS Cleared"},
            )
        )
        source = JobSource(name="Advisor Source", base_url="https://example.invalid", source_type="fixture")
        db.add(source)
        db.flush()
        job = Job(
            source_id=source.id,
            source_job_id="advisor-001",
            canonical_url="https://example.invalid/jobs/advisor-001",
            title="Automation Engineer",
            company_name="Example Ltd",
            location="Milton Keynes",
            remote_type="hybrid",
            description_text="Build workflow automation.",
            status="active",
        )
        db.add(job)
        db.flush()
        db.add(JobAnalysis(job_id=job.id, role_family="Automation Engineer"))
        db.commit()

    captured = {}

    class FakeProvider:
        provider_name = "groq"
        model_name = "llama-3.1-8b-instant"

        def send_chat(self, messages):
            captured["messages"] = messages
            return "Use the profile and job context."

    def override_get_db():
        with TestingSession() as db:
            yield db

    monkeypatch.setattr("app.api.ai.get_ai_provider", lambda: FakeProvider())
    app.dependency_overrides[get_db] = override_get_db
    try:
        response = TestClient(app).post("/ai/chat", json={"message": "Which jobs should I apply for first?"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "provider": "groq",
        "model": "llama-3.1-8b-instant",
        "response": "Use the profile and job context.",
    }
    prompt = captured["messages"][1]["content"]
    assert "Automation engineer with Power BI experience" in prompt
    assert "Power BI Timesheet Dashboard" in prompt
    assert "Automation Engineer at Example Ltd" in prompt
    assert "Which jobs should I apply for first?" in prompt
    assert "Do not invent experience" in captured["messages"][0]["content"]
