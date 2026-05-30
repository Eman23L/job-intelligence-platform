from types import SimpleNamespace
import json
from pathlib import Path
import sys

from app.diagnostics import assist_apply_probe
from app.diagnostics.write_assist_apply_probe_fallback import write_fallback_report


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


def fake_dependencies(job=None, user=None, profile=None):
    apply_agent = SimpleNamespace(
        _resolve_assist_apply_url_diagnostics=lambda candidate: {"selected_url": candidate.canonical_url},
        _resolve_assist_apply_url=lambda candidate: candidate.canonical_url,
    )
    return {
        "select": lambda model: SimpleNamespace(where=lambda *args: SimpleNamespace(order_by=lambda *items: object())),
        "settings": SimpleNamespace(queue_name="default", page_navigation_timeout_ms=1000),
        "Job": SimpleNamespace(__name__="Job"),
        "JobScore": SimpleNamespace(__name__="JobScore", job_id=SimpleNamespace(), user_id=SimpleNamespace(), scored_at=SimpleNamespace(desc=lambda: object())),
        "User": SimpleNamespace(__name__="User"),
        "SessionLocal": lambda: FakeSession(job, user, profile),
        "apply_agent": apply_agent,
        "browser_status": lambda: {"chromium_available": True},
        "chromium_executable_path": lambda: "/chromium",
        "get_profile": lambda db, candidate_user: profile,
        "redis_connection": lambda: SimpleNamespace(ping=lambda: True),
        "redis_url_host": lambda: "redis.internal",
    }


def test_assist_apply_probe_generates_report(tmp_path, monkeypatch) -> None:
    job = SimpleNamespace(id=123, title="AI Engineer", company_name="Example", canonical_url="https://www.jobserve.com/gb/en/job/ABC123", apply_url=None, source_job_id="ABC123", original_external_id=None)
    user = SimpleNamespace(id=1)
    profile = SimpleNamespace(cv_file_path="/tmp/cv.pdf", cv_file_bytes=None)

    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@example.com/db")
    monkeypatch.setenv("REDIS_URL", "redis://example.com:6379/0")
    monkeypatch.setattr(assist_apply_probe, "import_runtime_dependencies", lambda: fake_dependencies(job, user, profile))

    report = assist_apply_probe.build_report(123, 1, output_dir=tmp_path, run_browser_navigation=False)

    assert report["overall_status"] == "ok"
    assert report["failed_phase"] is None
    assert report["recommended_fix"]
    assert report["bootstrap"]["probe_module_imported"] is True
    assert report["phases"]["db_lookup"]["data"]["application_found"] is True
    assert any(path.endswith(".json") for path in report["artifact_paths"])
    assert any(path.endswith(".md") for path in report["artifact_paths"])
    assert (tmp_path / "latest_assist_apply_probe.json").is_file()
    assert (tmp_path / "latest_assist_apply_probe.md").is_file()


def test_assist_apply_probe_failed_phase_and_recommended_fix(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@example.com/db")
    monkeypatch.setenv("REDIS_URL", "redis://example.com:6379/0")
    monkeypatch.setattr(assist_apply_probe, "import_runtime_dependencies", lambda: fake_dependencies(None, SimpleNamespace(id=1), None))

    report = assist_apply_probe.build_report(999, 1, output_dir=tmp_path, run_browser_navigation=False)

    assert report["overall_status"] == "failed"
    assert report["failed_phase"] == "db_lookup"
    assert report["exact_error"] == "application_not_found"
    assert report["recommended_fix"]
    assert report["traceback"]
    latest = json.loads((tmp_path / "latest_assist_apply_probe.json").read_text(encoding="utf-8"))
    assert latest["failed_phase"] == "db_lookup"


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

    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@example.com/db")
    monkeypatch.setenv("REDIS_URL", "redis://example.com:6379/0")
    monkeypatch.setattr(assist_apply_probe, "import_runtime_dependencies", lambda: fake_dependencies(job, user, profile))

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


def test_assist_apply_probe_main_writes_report_on_exception(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["assist_apply_probe", "--application-id", "123", "--user-id", "1", "--safe-mode", "true"])

    def crash(*args, **kwargs):
        raise RuntimeError("probe exploded before phases")

    monkeypatch.setattr(assist_apply_probe, "build_report", crash)

    try:
        assist_apply_probe.main()
    except SystemExit as exc:
        assert exc.code == 1

    latest = tmp_path / "backend/runtime/assist_apply_diagnostics/latest_assist_apply_probe.json"
    markdown = tmp_path / "backend/runtime/assist_apply_diagnostics/latest_assist_apply_probe.md"
    assert latest.is_file()
    assert markdown.is_file()
    report = json.loads(latest.read_text(encoding="utf-8"))
    assert report["overall_status"] == "failed"
    assert report["failed_phase"] == "probe_unhandled_exception"
    assert report["exact_error"] == "probe exploded before phases"
    assert report["recommended_fix"]
    assert report["application_id"] == 123
    assert report["safe_mode"] is True
    assert report["timestamp"]


def test_assist_apply_workflow_artifact_path_matches_probe_output() -> None:
    workflow = (Path(__file__).resolve().parents[2] / ".github/workflows/assist-apply-diagnostics.yml").read_text(encoding="utf-8")
    fallback = (Path(__file__).resolve().parents[1] / "app/diagnostics/write_assist_apply_probe_fallback.py").read_text(encoding="utf-8")

    assert "backend/runtime/assist_apply_diagnostics/**" in workflow
    assert "latest_assist_apply_probe.json" in workflow + fallback
    assert "latest_assist_apply_probe.md" in workflow + fallback
    assert "probe_stdout.log" in workflow
    assert "probe_stderr.log" in workflow


def test_fallback_report_generation_when_probe_writes_no_files(tmp_path) -> None:
    report = assist_apply_probe.failure_report(
        123,
        1,
        safe_mode=True,
        failed_phase="probe_no_report",
        exact_error="assist_apply_probe exited before writing a report",
        traceback_text="not captured",
    )

    written = assist_apply_probe.write_report_files(report, tmp_path, timestamped=False)

    assert written["overall_status"] == "failed"
    assert written["failed_phase"] == "probe_no_report"
    assert (tmp_path / "latest_assist_apply_probe.json").is_file()
    assert (tmp_path / "latest_assist_apply_probe.md").is_file()


def test_assist_apply_probe_import_failure_writes_report(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@example.com/db")
    monkeypatch.setenv("REDIS_URL", "redis://example.com:6379/0")

    def crash():
        raise ImportError("db import exploded")

    monkeypatch.setattr(assist_apply_probe, "import_runtime_dependencies", crash)

    report = assist_apply_probe.build_report(123, 1, output_dir=tmp_path, run_browser_navigation=False)

    assert report["overall_status"] == "failed"
    assert report["failed_phase"] == "probe_import_failed"
    assert "db import exploded" in report["exact_error"]
    assert (tmp_path / "latest_assist_apply_probe.json").is_file()


def test_assist_apply_probe_argument_error_writes_report(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["assist_apply_probe"])

    try:
        assist_apply_probe.main()
    except SystemExit as exc:
        assert exc.code == 2

    latest = tmp_path / "backend/runtime/assist_apply_diagnostics/latest_assist_apply_probe.json"
    report = json.loads(latest.read_text(encoding="utf-8"))
    assert report["failed_phase"] == "probe_argument_error"
    assert "application-id" in report["exact_error"]


def test_assist_apply_probe_missing_configuration_writes_report(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)

    report = assist_apply_probe.build_report(123, 1, output_dir=tmp_path, run_browser_navigation=False)

    assert report["overall_status"] == "failed"
    assert report["failed_phase"] == "probe_missing_configuration"
    assert "DATABASE_URL" in report["exact_error"]
    assert "REDIS_URL" in report["exact_error"]


def test_assist_apply_workflow_fallback_includes_stdout_stderr_tail() -> None:
    workflow = (Path(__file__).resolve().parents[2] / ".github/workflows/assist-apply-diagnostics.yml").read_text(encoding="utf-8")
    fallback = (Path(__file__).resolve().parents[1] / "app/diagnostics/write_assist_apply_probe_fallback.py").read_text(encoding="utf-8")

    assert "write_assist_apply_probe_fallback.py" in workflow
    assert "stdout_tail" in fallback
    assert "stderr_tail" in fallback
    assert "probe_stdout.log" in workflow
    assert "probe_stderr.log" in workflow


def test_fallback_report_includes_stdout_stderr_tail(tmp_path) -> None:
    (tmp_path / "probe_stdout.log").write_text("stdout before\nstdout after", encoding="utf-8")
    (tmp_path / "probe_stderr.log").write_text("stderr before\nstderr after", encoding="utf-8")

    report = write_fallback_report("123", "true", tmp_path)

    assert report["failed_phase"] == "probe_no_report"
    assert "stdout after" in report["stdout_tail"]
    assert "stderr after" in report["stderr_tail"]
    assert "stdout after" in report["github_handoff"]["body"]
    assert (tmp_path / "latest_assist_apply_probe.json").is_file()
    assert (tmp_path / "latest_assist_apply_probe.md").is_file()
