from __future__ import annotations

import importlib.util
from types import SimpleNamespace
from pathlib import Path

from app.schemas.database import AssistApplyResult


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_jobserve_apply_debug.py"


def _load_script_module():
    spec = importlib.util.spec_from_file_location("run_jobserve_apply_debug", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_jobserve_visible_debug_script_imports_successfully() -> None:
    module = _load_script_module()

    assert module.parse_args
    assert module.run_shared_jobserve_flow


def test_jobserve_visible_debug_cli_args_parse_and_submit_defaults_false(tmp_path) -> None:
    module = _load_script_module()
    cv_path = tmp_path / "cv.pdf"
    cv_path.write_bytes(b"%PDF")

    args = module.parse_args(["--email", "me@example.com", "--cv-path", str(cv_path)])

    assert args.email == "me@example.com"
    assert args.cv_path == str(cv_path)
    assert args.keywords == "AI"
    assert args.location == "London"
    assert args.distance == "Within 50 miles"
    assert args.posted == "Within 7 days"
    assert args.job_type == "Any"
    assert args.submit is False
    assert args.pause_each_step is False
    assert args.slow_mo_ms == 500


def test_jobserve_visible_debug_cli_submit_sets_explicit_submit_mode(tmp_path) -> None:
    module = _load_script_module()
    cv_path = tmp_path / "cv.pdf"
    cv_path.write_bytes(b"%PDF")

    args = module.parse_args(["--email", "me@example.com", "--cv-path", str(cv_path), "--submit"])

    assert args.submit is True
    assert module.mode_from_args(args) == "submit_with_confirmation"


def test_jobserve_visible_debug_shared_flow_called_in_review_only(monkeypatch, tmp_path) -> None:
    module = _load_script_module()
    cv_path = tmp_path / "cv.pdf"
    cv_path.write_bytes(b"%PDF")
    args = module.parse_args(
        [
            "--email",
            "me@example.com",
            "--cv-path",
            str(cv_path),
            "--target-title",
            "AI Engineer",
            "--target-company",
            "Example Ltd",
        ]
    )
    captured = {}

    def fake_run(page, browser, candidates, profile, job_context, **kwargs):
        captured["mode"] = kwargs["mode"]
        captured["keep_open_for_review"] = kwargs["keep_open_for_review"]
        captured["email"] = candidates["email"].value
        captured["cv_file_path"] = profile.cv_file_path
        captured["job_context"] = job_context
        return AssistApplyResult(
            status="review_required",
            filled_fields=[],
            unfilled_fields=[],
            unfilled_required_fields=[],
            uploaded_cv=False,
            submitted=False,
            warnings=[],
            screenshot_path=None,
        )

    monkeypatch.setattr(module.apply_agent, "_run_jobserve_search_to_apply", fake_run)

    result = module.run_shared_jobserve_flow(object(), object(), args)

    assert result.submitted is False
    assert captured["mode"] == "review_only"
    assert captured["keep_open_for_review"] is True
    assert captured["email"] == "me@example.com"
    assert captured["cv_file_path"] == str(cv_path.resolve())
    assert captured["job_context"]["title"] == "AI Engineer"
    assert captured["job_context"]["company_name"] == "Example Ltd"


def test_jobserve_visible_debug_shared_flow_called_in_submit_mode(monkeypatch, tmp_path) -> None:
    module = _load_script_module()
    cv_path = tmp_path / "cv.pdf"
    cv_path.write_bytes(b"%PDF")
    args = module.parse_args(
        [
            "--email",
            "me@example.com",
            "--cv-path",
            str(cv_path),
            "--target-title",
            "AI Engineer",
            "--target-company",
            "Example Ltd",
            "--submit",
        ]
    )
    captured = {}

    def fake_run(page, browser, candidates, profile, job_context, **kwargs):
        captured["mode"] = kwargs["mode"]
        captured["keep_open_for_review"] = kwargs["keep_open_for_review"]
        captured["email"] = candidates["email"].value
        captured["cv_file_path"] = profile.cv_file_path
        captured["job_context"] = job_context
        return AssistApplyResult(
            status="submitted",
            filled_fields=[],
            unfilled_fields=[],
            unfilled_required_fields=[],
            uploaded_cv=True,
            submitted=True,
            warnings=[],
            screenshot_path=None,
            confirmation_text="Your application has been submitted.",
            registration_toggle_disabled=True,
            modal_closed=True,
        )

    monkeypatch.setattr(module.apply_agent, "_run_jobserve_search_to_apply", fake_run)

    result = module.run_shared_jobserve_flow(object(), object(), args)

    assert result.submitted is True
    assert captured["mode"] == "submit_with_confirmation"
    assert captured["keep_open_for_review"] is False
    assert captured["email"] == "me@example.com"
    assert captured["cv_file_path"] == str(cv_path.resolve())
    assert captured["job_context"]["title"] == "AI Engineer"
    assert captured["job_context"]["company_name"] == "Example Ltd"


def test_jobserve_visible_debug_submit_checkpoint_waits_for_enter_before_final_click(monkeypatch) -> None:
    module = _load_script_module()
    prompts = []
    progress = module.TerminalProgress(pause_each_step=True, submit_enabled=True)

    monkeypatch.setattr("builtins.input", lambda prompt="": prompts.append(prompt) or "")

    progress("jobserve_application_form_filled", {})
    progress(
        "jobserve_about_to_submit",
        {
            "submit_guard": {
                "email_filled": True,
                "email_value": "me@example.com",
                "working_status_selected": True,
                "working_status_value": "UK Citizen",
                "cv_uploaded": True,
                "identity_verified": True,
                "final_apply_click_enabled": True,
                "intended_job": {"title": "AI Engineer", "company_name": "Example Ltd"},
                "verified_job": {"title": "AI Engineer", "company": "Example Ltd"},
                "modal_job": {"title": "AI Engineer", "company": "Example Ltd"},
            }
        },
    )

    assert prompts == ["Ready to submit verified JobServe application. Press Enter to submit."]


def test_jobserve_visible_debug_review_only_checkpoint_uses_safe_prompt(monkeypatch) -> None:
    module = _load_script_module()
    prompts = []
    progress = module.TerminalProgress(pause_each_step=True, submit_enabled=False)

    monkeypatch.setattr("builtins.input", lambda prompt="": prompts.append(prompt) or "")

    progress("jobserve_application_form_filled", {})

    assert prompts == ["Press Enter to continue..."]


def test_jobserve_visible_debug_hardcoded_review_only_cannot_override_submit(monkeypatch, tmp_path) -> None:
    module = _load_script_module()
    cv_path = tmp_path / "cv.pdf"
    cv_path.write_bytes(b"%PDF")
    args = module.parse_args(["--email", "me@example.com", "--cv-path", str(cv_path), "--submit"])
    captured = {}

    def fake_run(page, browser, candidates, profile, job_context, **kwargs):
        captured["mode"] = kwargs["mode"]
        return AssistApplyResult(
            status="submitted",
            filled_fields=[],
            unfilled_fields=[],
            unfilled_required_fields=[],
            uploaded_cv=True,
            submitted=True,
            warnings=[],
            screenshot_path=None,
        )

    monkeypatch.setattr(module.apply_agent, "_run_jobserve_search_to_apply", fake_run)

    module.run_shared_jobserve_flow(SimpleNamespace(), SimpleNamespace(), args)

    assert captured["mode"] == module.mode_from_args(args)
    assert captured["mode"] != "review_only"
