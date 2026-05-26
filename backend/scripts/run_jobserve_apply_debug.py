from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.schemas.database import AssistApplyResult  # noqa: E402
from app.services import apply_agent  # noqa: E402


DEBUG_ROOT = BACKEND_ROOT / "runtime" / "jobserve-debug"
VIDEO_DIR = DEBUG_ROOT / "videos"
TRACE_DIR = DEBUG_ROOT / "traces"


STEP_MESSAGES = {
    "jobserve_search_page_loaded": "opening search page",
    "jobserve_search_keyword_filled": "filling keyword",
    "jobserve_search_location_filled": "filling location",
    "jobserve_search_distance_selected": "selecting distance",
    "jobserve_search_industries_selected": "selecting industries",
    "jobserve_search_posted_selected": "selecting posted dropdown",
    "jobserve_search_job_type_selected": "selecting job type",
    "jobserve_search_remote_only_unchecked": "unchecking remote only",
    "jobserve_search_form_filled": "search form filled",
    "jobserve_search_submitted": "clicking search",
    "jobserve_target_job_selected": "matching job",
    "jobserve_apply_modal_wait_complete": "opening modal",
    "jobserve_apply_email_filled": "filling email",
    "jobserve_apply_confirmation_email_checked": "keeping confirmation email checked",
    "jobserve_apply_working_status_selected": "selecting UK Citizen",
    "jobserve_apply_cv_uploaded": "uploading CV",
    "jobserve_application_form_filled": "ready to submit",
    "jobserve_final_apply_clicked": "clicking final Apply",
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the JobServe apply flow in a visible local Playwright browser.")
    parser.add_argument("--email", required=True, help="Email address to fill in the JobServe application modal.")
    parser.add_argument("--cv-path", required=True, help="Local CV file path to upload.")
    parser.add_argument("--keywords", default="AI")
    parser.add_argument("--location", default="London")
    parser.add_argument("--distance", default="Within 50 miles")
    parser.add_argument("--posted", default="Within 7 days")
    parser.add_argument("--job-type", default="Any")
    parser.add_argument("--target-title")
    parser.add_argument("--target-company")
    parser.add_argument("--target-reference")
    parser.add_argument("--submit", action="store_true", help="Click the final JobServe Apply button. Defaults to review-only.")
    parser.add_argument("--pause-each-step", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--slow-mo-ms", type=int, default=500)
    parser.add_argument("--devtools", action="store_true")
    return parser.parse_args(argv)


def build_local_profile(args: argparse.Namespace) -> SimpleNamespace:
    cv_path = Path(args.cv_path).expanduser().resolve()
    return SimpleNamespace(
        email=args.email,
        work_status_uk="UK Citizen",
        cv_file_path=str(cv_path),
        cv_file_name=cv_path.name,
        cv_file_bytes=None,
        cv_file_mime_type=mimetypes.guess_type(str(cv_path))[0],
        cv_file_size=cv_path.stat().st_size if cv_path.exists() else None,
        location_preference=args.location,
        availability_notice=None,
        salary_expectation=None,
        salary_expectation_gbp=None,
        travel_distance=None,
        travel_distance_miles=None,
        preferences={
            "jobserve_search_keywords": args.keywords,
            "jobserve_search_location": args.location,
            "jobserve_search_distance": args.distance,
            "jobserve_posted_within": args.posted,
            "jobserve_job_type": args.job_type,
            "jobserve_working_status": "UK Citizen",
        },
    )


def build_job_context(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "job_id": None,
        "title": args.target_title,
        "original_title": args.target_title,
        "company_name": args.target_company,
        "original_company": args.target_company,
        "source_job_id": args.target_reference,
        "original_external_id": args.target_reference,
    }


def run_shared_jobserve_flow(page, browser, args: argparse.Namespace, progress_callback: Callable[[str, dict[str, Any]], None] | None = None) -> AssistApplyResult:
    profile = build_local_profile(args)
    user = SimpleNamespace(email=args.email)
    candidates = apply_agent.profile_field_candidates(user, profile)
    mode = "submit_with_confirmation" if args.submit else "review_only"
    return apply_agent._run_jobserve_search_to_apply(
        page,
        browser,
        candidates,
        profile,
        build_job_context(args),
        mode=mode,
        keep_open_for_review=not args.submit,
        debug_mode=True,
        profile_diagnostics=apply_agent.profile_debug_payload(user, profile, candidates),
        progress_callback=progress_callback,
    )


class TerminalProgress:
    def __init__(self, *, pause_each_step: bool) -> None:
        self.pause_each_step = pause_each_step
        self.seen: set[str] = set()

    def __call__(self, step: str, payload: dict[str, Any]) -> None:
        message = STEP_MESSAGES.get(step, step.replace("_", " "))
        suffix = ""
        if "succeeded" in payload:
            suffix = " ok" if payload.get("succeeded") else " failed"
        elif "jobserve_flow_diagnostics" in payload:
            flow = payload["jobserve_flow_diagnostics"]
            selected = flow.get("selected_job") if isinstance(flow, dict) else None
            if selected:
                suffix = f" selected={selected.get('title') or selected.get('text') or selected.get('href')}"
        print(f"[jobserve-debug] {message}{suffix}", flush=True)
        if self.pause_each_step and step not in self.seen:
            self.seen.add(step)
            input("Press Enter to continue...")


def run_visible_browser(args: argparse.Namespace) -> AssistApplyResult:
    cv_path = Path(args.cv_path).expanduser().resolve()
    if not cv_path.exists():
        raise FileNotFoundError(f"CV file does not exist: {cv_path}")

    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    TRACE_DIR.mkdir(parents=True, exist_ok=True)
    trace_path = TRACE_DIR / f"jobserve-visible-{int(time.time())}.zip"

    print("[jobserve-debug] opening search page", flush=True)
    print(f"[jobserve-debug] mode={'submit' if args.submit else 'review_only'}", flush=True)
    print(f"[jobserve-debug] video directory: {VIDEO_DIR}", flush=True)
    print(f"[jobserve-debug] trace path: {trace_path}", flush=True)

    from playwright.sync_api import sync_playwright

    result: AssistApplyResult | None = None
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False, slow_mo=args.slow_mo_ms, devtools=args.devtools)
        context = browser.new_context(record_video_dir=str(VIDEO_DIR), viewport={"width": 1440, "height": 1000})
        context.tracing.start(screenshots=True, snapshots=True, sources=True)
        page = context.new_page()
        try:
            result = run_shared_jobserve_flow(page, browser, args, TerminalProgress(pause_each_step=args.pause_each_step))
            if result.submitted:
                print("[jobserve-debug] submitted confirmation detected", flush=True)
            else:
                print("[jobserve-debug] ready to submit; review-only mode did not click final Apply", flush=True)
            input("Press Enter to close the visible browser...")
            return result
        finally:
            context.tracing.stop(path=str(trace_path))
            context.close()
            browser.close()
            print(f"[jobserve-debug] trace saved: {trace_path}", flush=True)
            print("[jobserve-debug] open trace with:", flush=True)
            print(f"python -m playwright show-trace {trace_path}", flush=True)
            print(f"[jobserve-debug] videos saved under: {VIDEO_DIR}", flush=True)
            if result is not None:
                print("[jobserve-debug] result:", json.dumps(result.model_dump(), default=str, indent=2)[:8000], flush=True)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    run_visible_browser(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
