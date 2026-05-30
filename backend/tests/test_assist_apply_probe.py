from types import SimpleNamespace

from app.diagnostics import assist_apply_probe


class FakeSession:
    def __init__(self, job, user, profile):
        self.job = job
        self.user = user
        self.profile = profile

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, model, ident):
        name = getattr(model, "__name__", "")
        if name == "Job":
            return self.job
        if name == "User":
            return self.user
        return None

    def scalar(self, statement):
        return None


def test_assist_apply_probe_generates_report(tmp_path, monkeypatch) -> None:
    job = SimpleNamespace(id=123, title="AI Engineer", company_name="Example", canonical_url="https://www.jobserve.com/gb/en/job/ABC123", apply_url=None, source_job_id="ABC123", original_external_id=None)
    user = SimpleNamespace(id=1)
    profile = SimpleNamespace(cv_file_path="/tmp/cv.pdf", cv_file_bytes=None)

    monkeypatch.setattr(assist_apply_probe, "redis_connection", lambda: SimpleNamespace(ping=lambda: True))
    monkeypatch.setattr(assist_apply_probe, "redis_url_host", lambda: "redis.internal")
    monkeypatch.setattr(assist_apply_probe, "SessionLocal", lambda: FakeSession(job, user, profile))
    monkeypatch.setattr(assist_apply_probe, "get_profile", lambda db, candidate_user: profile)
    monkeypatch.setattr(assist_apply_probe, "browser_status", lambda: {"chromium_available": True})
    monkeypatch.setattr(assist_apply_probe, "chromium_executable_path", lambda: "/chromium")
    monkeypatch.setattr(assist_apply_probe.apply_agent, "_resolve_assist_apply_url_diagnostics", lambda candidate: {"selected_url": candidate.canonical_url})
    monkeypatch.setattr(assist_apply_probe.apply_agent, "_resolve_assist_apply_url", lambda candidate: candidate.canonical_url)

    report = assist_apply_probe.build_report(123, 1, output_dir=tmp_path, run_browser_navigation=False)

    assert report["overall_status"] == "ok"
    assert report["failed_phase"] is None
    assert report["recommended_fix"]
    assert report["phases"]["db_lookup"]["data"]["application_found"] is True
    assert any(path.endswith(".json") for path in report["artifact_paths"])
    assert any(path.endswith(".md") for path in report["artifact_paths"])


def test_assist_apply_probe_failed_phase_and_recommended_fix(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(assist_apply_probe, "redis_connection", lambda: SimpleNamespace(ping=lambda: True))
    monkeypatch.setattr(assist_apply_probe, "redis_url_host", lambda: "redis.internal")
    monkeypatch.setattr(assist_apply_probe, "SessionLocal", lambda: FakeSession(None, SimpleNamespace(id=1), None))
    monkeypatch.setattr(assist_apply_probe, "browser_status", lambda: {"chromium_available": True})
    monkeypatch.setattr(assist_apply_probe, "chromium_executable_path", lambda: "/chromium")

    report = assist_apply_probe.build_report(999, 1, output_dir=tmp_path, run_browser_navigation=False)

    assert report["overall_status"] == "failed"
    assert report["failed_phase"] == "db_lookup"
    assert report["exact_error"] == "application_not_found"
    assert report["recommended_fix"]
    assert report["traceback"]


def test_assist_apply_probe_redacts_secrets() -> None:
    value = assist_apply_probe.redact_value(
        {
            "DATABASE_URL": "postgresql://user:password@example.com/db",
            "REDIS_URL": "redis://:secret@example.com:6379/0",
            "RENDER_WEB_DEPLOY_HOOK_URL": "https://api.render.com/deploy/srv-secret",
            "normal": "visible",
        }
    )

    assert value["DATABASE_URL"] == "[redacted]"
    assert value["REDIS_URL"] == "[redacted]"
    assert value["RENDER_WEB_DEPLOY_HOOK_URL"] == "[redacted]"
    assert value["normal"] == "visible"


def test_assist_apply_probe_safe_mode_does_not_submit(tmp_path, monkeypatch) -> None:
    job = SimpleNamespace(id=123, title="AI Engineer", company_name="Example", canonical_url="https://www.jobserve.com/gb/en/job/ABC123", apply_url=None, source_job_id="ABC123", original_external_id=None)
    user = SimpleNamespace(id=1)
    profile = SimpleNamespace(cv_file_path="/tmp/cv.pdf", cv_file_bytes=None)

    monkeypatch.setattr(assist_apply_probe, "redis_connection", lambda: SimpleNamespace(ping=lambda: True))
    monkeypatch.setattr(assist_apply_probe, "redis_url_host", lambda: "redis.internal")
    monkeypatch.setattr(assist_apply_probe, "SessionLocal", lambda: FakeSession(job, user, profile))
    monkeypatch.setattr(assist_apply_probe, "get_profile", lambda db, candidate_user: profile)
    monkeypatch.setattr(assist_apply_probe, "browser_status", lambda: {"chromium_available": True})
    monkeypatch.setattr(assist_apply_probe, "chromium_executable_path", lambda: "/chromium")
    monkeypatch.setattr(assist_apply_probe.apply_agent, "_resolve_assist_apply_url_diagnostics", lambda candidate: {"selected_url": candidate.canonical_url})
    monkeypatch.setattr(assist_apply_probe.apply_agent, "_resolve_assist_apply_url", lambda candidate: candidate.canonical_url)

    report = assist_apply_probe.build_report(123, 1, safe_mode=True, submit_allowed=False, output_dir=tmp_path, run_browser_navigation=False)

    assert report["phases"]["submit_guard"]["data"]["safe_mode_prevents_final_submit"] is True
    assert report["phases"]["modal_form_detection"]["data"]["safe_mode_no_final_apply_click"] is True


def test_assist_apply_probe_github_handoff_payload_valid() -> None:
    report = {
        "application_id": 123,
        "failed_phase": "db_lookup",
        "exact_error": "application_not_found",
        "traceback": "Traceback...",
        "recommended_fix": "Fix DB lookup.",
    }

    payload = assist_apply_probe.github_handoff_payload(report, ["report.json", "report.md"])

    assert payload["title"] == "Assist apply diagnostics failed for application 123"
    assert "@codex fix this failure" in payload["body"]
    assert "failed_phase: db_lookup" in payload["body"]
    assert "recommended_fix: Fix DB lookup." in payload["body"]
    assert "report.json" in payload["body"]
    assert "assist-apply-diagnostics" in payload["labels"]
