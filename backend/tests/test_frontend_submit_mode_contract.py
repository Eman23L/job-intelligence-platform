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
