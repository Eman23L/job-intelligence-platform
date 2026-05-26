from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

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
    assert args.auto_submit is False
    assert args.pause_each_step is False
    assert args.pause_application_steps is False
    assert args.slow_mo_ms == 500


def test_jobserve_visible_debug_valid_cv_path_resolves(tmp_path) -> None:
    module = _load_script_module()
    cv_path = tmp_path / "manuel_cv.pdf"
    cv_path.write_bytes(b"%PDF")
    args = module.parse_args(["--email", "me@example.com", "--cv-path", str(cv_path)])

    resolved = module.resolve_cv_path(args, search_dirs=[])

    assert resolved == cv_path.resolve()
    assert args.cv_path == str(cv_path.resolve())


def test_jobserve_visible_debug_missing_cv_path_gives_friendly_error(capsys, tmp_path) -> None:
    module = _load_script_module()
    missing = tmp_path / "manuel_Bamgbala_CV (1).pdf"
    args = module.parse_args(["--email", "me@example.com", "--cv-path", str(missing)])

    try:
        module.resolve_cv_path(args, search_dirs=[])
    except module.CVPathError as exc:
        assert "No likely CV file found" in str(exc)
    else:
        raise AssertionError("Expected CVPathError")

    output = capsys.readouterr().out
    assert f"CV file not found at: {missing.resolve()}" in output
    assert "current working directory:" in output
    assert "parent folder exists:" in output


def test_jobserve_visible_debug_cv_search_finds_one_cv_automatically(capsys, tmp_path) -> None:
    module = _load_script_module()
    cv_path = tmp_path / "manuel-bamgbala-cv.pdf"
    cv_path.write_bytes(b"%PDF")
    args = module.parse_args(["--email", "me@example.com", "--cv-search"])

    resolved = module.resolve_cv_path(args, search_dirs=[tmp_path])

    assert resolved == cv_path.resolve()
    assert args.cv_path == str(cv_path.resolve())
    assert "Using discovered CV:" in capsys.readouterr().out


def test_jobserve_visible_debug_multiple_cvs_prompt_selection(tmp_path) -> None:
    module = _load_script_module()
    first = tmp_path / "manuel-cv.pdf"
    second = tmp_path / "emmanuel-resume.docx"
    first.write_bytes(b"%PDF")
    second.write_bytes(b"DOCX")
    args = module.parse_args(["--email", "me@example.com", "--cv-search"])

    resolved = module.resolve_cv_path(args, input_func=lambda prompt: "2", search_dirs=[tmp_path])

    assert resolved in {first.resolve(), second.resolve()}
    assert args.cv_path == str(resolved)


def test_jobserve_visible_debug_remembered_cv_path_is_reused(tmp_path) -> None:
    module = _load_script_module()
    cv_path = tmp_path / "manuel-cv.pdf"
    remember_path = tmp_path / "remember.txt"
    cv_path.write_bytes(b"%PDF")
    remember_path.write_text(str(cv_path), encoding="utf-8")
    args = module.parse_args(["--email", "me@example.com"])

    resolved = module.resolve_cv_path(args, search_dirs=[], remember_path=remember_path)

    assert resolved == cv_path.resolve()
    assert args.cv_path == str(cv_path.resolve())


def test_jobserve_visible_debug_invalid_remembered_path_falls_back_to_search(capsys, tmp_path) -> None:
    module = _load_script_module()
    cv_path = tmp_path / "manuel-cv.pdf"
    remember_path = tmp_path / "remember.txt"
    cv_path.write_bytes(b"%PDF")
    remember_path.write_text(str(tmp_path / "missing.pdf"), encoding="utf-8")
    args = module.parse_args(["--email", "me@example.com"])

    resolved = module.resolve_cv_path(args, search_dirs=[tmp_path], remember_path=remember_path)

    assert resolved == cv_path.resolve()
    output = capsys.readouterr().out
    assert "Remembered CV path is invalid" in output
    assert "Using discovered CV:" in output


def test_jobserve_visible_debug_cli_submit_sets_explicit_submit_mode(tmp_path) -> None:
    module = _load_script_module()
    cv_path = tmp_path / "cv.pdf"
    cv_path.write_bytes(b"%PDF")

    args = module.parse_args(["--email", "me@example.com", "--cv-path", str(cv_path), "--submit", "--intended-title", "AI Engineer", "--intended-company", "Example Ltd"])

    assert args.submit is True
    assert module.mode_from_args(args) == "submit_with_confirmation"


def test_jobserve_visible_debug_cli_auto_submit_sets_explicit_submit_mode(tmp_path) -> None:
    module = _load_script_module()
    cv_path = tmp_path / "cv.pdf"
    cv_path.write_bytes(b"%PDF")

    args = module.parse_args(["--email", "me@example.com", "--cv-path", str(cv_path), "--auto-submit"])

    assert args.auto_submit is True
    assert module.submit_requested(args) is True
    assert module.mode_from_args(args) == "submit_with_confirmation"


def test_jobserve_visible_debug_submit_without_identity_gives_friendly_early_error(tmp_path) -> None:
    module = _load_script_module()
    cv_path = tmp_path / "cv.pdf"
    cv_path.write_bytes(b"%PDF")
    args = module.parse_args(["--email", "me@example.com", "--cv-path", str(cv_path), "--submit"])

    try:
        module.validate_local_submit_identity(args)
    except module.LocalSubmitIdentityError as exc:
        assert "Submit mode needs intended job identity" in str(exc)
    else:
        raise AssertionError("Expected LocalSubmitIdentityError")


def test_jobserve_visible_debug_submit_with_intended_args_proceeds(tmp_path) -> None:
    module = _load_script_module()
    cv_path = tmp_path / "cv.pdf"
    cv_path.write_bytes(b"%PDF")
    args = module.parse_args(
        [
            "--email",
            "me@example.com",
            "--cv-path",
            str(cv_path),
            "--submit",
            "--intended-title",
            "AI Engineer",
            "--intended-company",
            "Opus Recruitment Solutions Ltd",
        ]
    )

    module.validate_local_submit_identity(args)
    context = module.build_job_context(args)

    assert context["identity_source"] == "manual_args"
    assert context["title"] == "AI Engineer"
    assert context["company_name"] == "Opus Recruitment Solutions Ltd"


def test_jobserve_visible_debug_submit_with_use_current_selected_job_proceeds(tmp_path) -> None:
    module = _load_script_module()
    cv_path = tmp_path / "cv.pdf"
    cv_path.write_bytes(b"%PDF")
    args = module.parse_args(["--email", "me@example.com", "--cv-path", str(cv_path), "--submit", "--use-current-selected-job"])

    module.validate_local_submit_identity(args)
    context = module.build_job_context(args)

    assert context["identity_source"] == "current_selected_job"
    assert context["title"] is None


def test_jobserve_visible_debug_use_current_selected_aliases_parse(tmp_path) -> None:
    module = _load_script_module()
    cv_path = tmp_path / "cv.pdf"
    cv_path.write_bytes(b"%PDF")

    for flag in ["--use-current-selected-job", "--use-selected-job", "--use-visible-job"]:
        args = module.parse_args(["--email", "me@example.com", "--cv-path", str(cv_path), "--submit", flag])
        assert args.use_current_selected_job is True
        module.validate_local_submit_identity(args)


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


def test_jobserve_visible_debug_use_current_selected_job_flag_is_passed(monkeypatch, tmp_path) -> None:
    module = _load_script_module()
    cv_path = tmp_path / "cv.pdf"
    cv_path.write_bytes(b"%PDF")
    args = module.parse_args(["--email", "me@example.com", "--cv-path", str(cv_path), "--submit", "--use-current-selected-job"])
    captured = {}

    def fake_run(page, browser, candidates, profile, job_context, **kwargs):
        captured["use_current_selected_job_as_intended"] = kwargs["use_current_selected_job_as_intended"]
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
        )

    monkeypatch.setattr(module.apply_agent, "_run_jobserve_search_to_apply", fake_run)

    module.run_shared_jobserve_flow(SimpleNamespace(), SimpleNamespace(), args)

    assert captured["use_current_selected_job_as_intended"] is True
    assert captured["job_context"]["identity_source"] == "current_selected_job"


def test_jobserve_visible_debug_job_url_uses_direct_modal_flow(monkeypatch, tmp_path) -> None:
    module = _load_script_module()
    cv_path = tmp_path / "cv.pdf"
    cv_path.write_bytes(b"%PDF")
    args = module.parse_args(
        [
            "--email",
            "me@example.com",
            "--cv-path",
            str(cv_path),
            "--submit",
            "--job-url",
            "https://www.jobserve.com/gb/en/job/D8DF",
            "--intended-title",
            "AI Engineer",
            "--intended-company",
            "Example Ltd",
        ]
    )
    captured = {}

    def fake_modal(page, browser, candidates, profile, **kwargs):
        captured.update(kwargs)
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

    monkeypatch.setattr(module.apply_agent, "_run_jobserve_modal", fake_modal)

    module.run_shared_jobserve_flow(SimpleNamespace(), SimpleNamespace(), args)

    assert captured["direct_url"] == "https://www.jobserve.com/gb/en/job/D8DF"
    assert captured["job_context"]["canonical_url"] == "https://www.jobserve.com/gb/en/job/D8DF"
    assert captured["job_context"]["title"] == "AI Engineer"


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

    assert prompts == ["Ready to click JobServe modal Apply. Press Enter to continue."]


def test_jobserve_visible_debug_pause_application_steps_only_pauses_modal_steps(monkeypatch) -> None:
    module = _load_script_module()
    prompts = []
    progress = module.TerminalProgress(pause_each_step=False, submit_enabled=True, pause_application_steps=True)

    monkeypatch.setattr("builtins.input", lambda prompt="": prompts.append(prompt) or "")

    progress("jobserve_search_form_filled", {})
    progress("jobserve_apply_email_filled", {"succeeded": True, "email_value": "me@example.com"})

    assert prompts == ["Press Enter to continue..."]


def test_jobserve_visible_debug_pause_application_steps_submit_prompt(monkeypatch) -> None:
    module = _load_script_module()
    prompts = []
    progress = module.TerminalProgress(pause_each_step=False, submit_enabled=True, pause_application_steps=True)

    monkeypatch.setattr("builtins.input", lambda prompt="": prompts.append(prompt) or "")

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

    assert prompts == ["Ready to click JobServe modal Apply. Press Enter to continue."]


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


def test_jobserve_visible_debug_startup_output_includes_selected_mode(capsys, tmp_path) -> None:
    module = _load_script_module()
    cv_path = tmp_path / "cv.pdf"
    cv_path.write_bytes(b"%PDF")
    args = module.parse_args(["--email", "me@example.com", "--cv-path", str(cv_path), "--submit", "--intended-title", "AI Engineer", "--intended-company", "Example Ltd"])

    module.print_startup_config(args, tmp_path / "trace.zip", cv_path.resolve())

    output = capsys.readouterr().out
    assert "submit flag received: true" in output
    assert "mode selected: submit_with_confirmation" in output
    assert "use_current_selected_job flag received: false" in output
    assert "identity source: manual_args" in output
    assert "intended title: AI Engineer" in output
    assert "intended company: Example Ltd" in output
    assert "email: me@example.com" in output
    assert f"resolved CV path: {cv_path.resolve()}" in output
    assert "CV exists/readable: true" in output
    assert "CV size: 4" in output
    assert "final apply click enabled: true" in output


def test_jobserve_visible_debug_startup_output_includes_review_mode(capsys, tmp_path) -> None:
    module = _load_script_module()
    cv_path = tmp_path / "cv.pdf"
    cv_path.write_bytes(b"%PDF")
    args = module.parse_args(["--email", "me@example.com", "--cv-path", str(cv_path)])

    module.print_startup_config(args, tmp_path / "trace.zip", cv_path.resolve())

    output = capsys.readouterr().out
    assert "submit flag received: false" in output
    assert "use_current_selected_job flag received: false" in output
    assert "mode selected: review_only" in output
    assert "identity source: missing" in output
    assert "email: me@example.com" in output
    assert "final apply click enabled: false" in output
