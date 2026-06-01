from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_submit_jobserve_application_button_sends_submit_mode() -> None:
    page = (REPO_ROOT / "frontend" / "src" / "app" / "applications" / "page.tsx").read_text()

    assert 'assistApply(item, "review_only")' in page
    assert 'Submit JobServe application' in page
    assert 'assistApply(item, "submit_with_confirmation")' in page


def test_frontend_api_posts_assist_apply_mode() -> None:
    api = (REPO_ROOT / "frontend" / "src" / "lib" / "api.ts").read_text()

    assert 'mode: "review_only" | "submit_with_confirmation" = "review_only"' in api
    assert "JSON.stringify({ mode, debug_mode: debugMode })" in api


def test_submit_failure_triggers_safe_diagnostics() -> None:
    page = (REPO_ROOT / "frontend" / "src" / "app" / "applications" / "page.tsx").read_text()
    api = (REPO_ROOT / "frontend" / "src" / "lib" / "api.ts").read_text()

    assert "triggerSubmitFailureDiagnostic" in page
    assert 'mode === "submit_with_confirmation" && submitFailed(displayedResult)' in page
    assert "api.runAutonomousRealSubmit()" in page
    assert "startAssistApplyDiagnostic" in api
    assert "`/applications/${id}/assist-apply/diagnostics`" in api
    assert "submit_allowed" not in page


def test_diagnostic_summary_rendered_in_assist_modal() -> None:
    page = (REPO_ROOT / "frontend" / "src" / "app" / "applications" / "page.tsx").read_text()

    assert "Safe diagnostic passed. The failure is likely in the final form-fill/submit stage, not job lookup/browser/worker startup." in page
    assert "Diagnostic status" in page
    assert "Failed phase" in page
    assert "Exact error" in page
    assert "Recommended fix" in page
    assert "Diagnostic artifacts" in page


def test_autonomous_real_submit_status_is_visible() -> None:
    page = (REPO_ROOT / "frontend" / "src" / "app" / "applications" / "page.tsx").read_text()
    api = (REPO_ROOT / "frontend" / "src" / "lib" / "api.ts").read_text()

    assert "Autonomous real-submit mode" in page
    assert "Max submits per run" in page
    assert "Last result" in page
    assert "runAutonomousRealSubmit" in page
    assert "autonomousRealSubmitStatus" in api


def test_ui_shows_orchestration_summary() -> None:
    page = (REPO_ROOT / "frontend" / "src" / "app" / "applications" / "page.tsx").read_text()

    assert "Autonomous orchestration summary" in page
    assert "Orchestration steps" in page
    assert "Final outcome" in page
    assert "Codex handoff" in page
    assert "GitHub issue URL" in page
    assert "Open Codex handoff issue" in page
    assert "Focused application" in page
    assert "Attempt" in page
    assert "Retry same application after deploy" in page
