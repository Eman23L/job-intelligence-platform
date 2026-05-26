from __future__ import annotations

import importlib.util
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
