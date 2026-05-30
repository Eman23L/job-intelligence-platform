from __future__ import annotations

from app.config import settings
from app.services import codex_handoff


def test_codex_handoff_creates_issue(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(settings, "github_token", "ghp_secret")
    monkeypatch.setattr(settings, "github_repository", "owner/repo")

    def fake_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        if method == "GET" and path.endswith("/issues"):
            return []
        if method == "POST" and path.endswith("/issues"):
            body = kwargs["json"]["body"]
            assert "@codex fix this failure" in body
            assert "failed_phase: jobserve_navigation" in body
            assert "modal missing" in body
            return {"number": 10, "html_url": "https://github.com/owner/repo/issues/10"}
        raise AssertionError((method, path))

    monkeypatch.setattr(codex_handoff, "_github_request", fake_request)

    result = codex_handoff.create_or_update_codex_handoff(
        {"application_id": 645, "failed_phase": "jobserve_navigation", "exact_error": "modal missing", "recommended_fix": "Fix selector."}
    )

    assert result["status"] == "created"
    assert result["issue_url"].endswith("/10")
    assert calls[-1][2]["json"]["labels"] == ["codex", "autonomous-canary", "assist-apply"]


def test_duplicate_failure_updates_existing_issue(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(settings, "github_token", "ghp_secret")
    monkeypatch.setattr(settings, "github_repository", "owner/repo")

    def fake_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        if method == "GET" and path.endswith("/issues"):
            return [{"number": 10, "title": "Autonomous canary failure for application 645: jobserve_navigation", "html_url": "https://github.com/owner/repo/issues/10", "body": ""}]
        if method == "GET" and path.endswith("/comments"):
            return []
        if method == "POST" and path.endswith("/comments"):
            assert codex_handoff.ATTEMPT_MARKER in kwargs["json"]["body"]
            return {}
        raise AssertionError((method, path))

    monkeypatch.setattr(codex_handoff, "_github_request", fake_request)

    result = codex_handoff.create_or_update_codex_handoff(
        {"application_id": 645, "failed_phase": "jobserve_navigation", "exact_error": "modal missing", "recommended_fix": "Fix selector."}
    )

    assert result["status"] == "updated"
    assert any(call[0] == "POST" and call[1].endswith("/comments") for call in calls)


def test_successful_post_deploy_verification_comments_and_closes_issue(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(settings, "github_token", "ghp_secret")
    monkeypatch.setattr(settings, "github_repository", "owner/repo")

    def fake_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        if method == "GET" and path.endswith("/issues"):
            return [{"number": 10, "title": "Autonomous canary failure for application 645: jobserve_navigation", "html_url": "https://github.com/owner/repo/issues/10"}]
        if method in {"POST", "PATCH"}:
            return {}
        raise AssertionError((method, path))

    monkeypatch.setattr(codex_handoff, "_github_request", fake_request)

    result = codex_handoff.create_or_update_codex_handoff({"overall_status": "ok", "application_id": 645, "latest_commit_sha": "abc"})

    assert result["status"] == "closed"
    assert any(call[0] == "PATCH" and call[2]["json"]["state"] == "closed" for call in calls)


def test_secrets_and_emails_are_redacted() -> None:
    redacted = codex_handoff.redact(
        {
            "DATABASE_URL": "postgresql://user:secret@example.com:5432/db",
            "message": "Bearer ghp_secret for user@example.com",
            "nested": {"github_token": "secret-value"},
        }
    )

    assert redacted["DATABASE_URL"] == "[redacted]"
    assert "ghp_secret" not in redacted["message"]
    assert "user@example.com" not in redacted["message"]
    assert redacted["nested"]["github_token"] == "[redacted]"


def test_loop_guard_stops_repeated_same_failure(monkeypatch) -> None:
    monkeypatch.setattr(settings, "github_token", "ghp_secret")
    monkeypatch.setattr(settings, "github_repository", "owner/repo")

    def fake_request(method, path, **kwargs):
        if method == "GET" and path.endswith("/issues"):
            return [
                {
                    "number": 10,
                    "title": "Autonomous canary failure for application 645: jobserve_navigation",
                    "html_url": "https://github.com/owner/repo/issues/10",
                    "body": "modal missing",
                }
            ]
        if method == "GET" and path.endswith("/comments"):
            return [{"body": f"{codex_handoff.ATTEMPT_MARKER}\nmodal missing"}]
        raise AssertionError((method, path))

    monkeypatch.setattr(codex_handoff, "_github_request", fake_request)

    result = codex_handoff.create_or_update_codex_handoff(
        {"application_id": 645, "failed_phase": "jobserve_navigation", "exact_error": "modal missing", "recommended_fix": "Fix selector."}
    )

    assert result["status"] == "loop_guard_stopped"
    assert result["error"] == "same_exact_error_repeated_after_two_deploys"
