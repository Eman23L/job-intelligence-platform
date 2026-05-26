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
REMEMBERED_CV_PATH = BACKEND_ROOT / "runtime" / "jobserve_debug_cv_path.txt"
CV_EXTENSIONS = {".pdf", ".doc", ".docx"}
CV_KEYWORDS = ("cv", "resume", "manuel", "bamgbala", "emmanuel")


STEP_MESSAGES = {
    "jobserve_search_page_loaded": "opening search page",
    "jobserve_search_keyword_filled": "filling keyword",
    "jobserve_search_location_filled": "filling location",
    "jobserve_search_distance_selected": "selecting distance dropdown",
    "jobserve_search_distance_dropdown_opened": "distance dropdown opened",
    "jobserve_search_distance_option_clicked": "clicked option Within 50 miles",
    "jobserve_search_distance_already_selected": "distance already selected",
    "jobserve_search_industries_selected": "selecting industries",
    "jobserve_search_industries_dropdown_opened": "industries dropdown opened",
    "jobserve_search_industries_option_clicked": "clicked option Select All",
    "jobserve_search_posted_selected": "selecting posted dropdown",
    "jobserve_search_posted_dropdown_opened": "posted dropdown opened",
    "jobserve_search_posted_option_clicked": "clicked option Within 7 days",
    "jobserve_search_job_type_selected": "selecting job type",
    "jobserve_search_job_type_dropdown_opened": "job type dropdown opened",
    "jobserve_search_job_type_option_clicked": "clicked option Any",
    "jobserve_search_remote_only_unchecked": "unchecking remote only",
    "jobserve_search_form_filled": "search form filled",
    "jobserve_search_submitted": "clicking search",
    "jobserve_results_loaded": "results loaded",
    "jobserve_current_selected_job_used_as_intended": "using current selected JobServe result as intended job for local debug submit",
    "jobserve_target_job_selected": "matching job",
    "jobserve_apply_button_clicked": "apply button clicked",
    "jobserve_apply_modal_wait_complete": "opening modal",
    "jobserve_apply_email_filled": "filling email",
    "jobserve_apply_confirmation_email_checked": "keeping confirmation email checked",
    "jobserve_apply_working_status_selected": "selecting UK Citizen",
    "jobserve_apply_cv_uploaded": "uploading CV",
    "jobserve_application_form_filled": "ready to submit",
    "jobserve_about_to_submit": "About to submit. Press Enter to continue.",
    "jobserve_first_apply_clicked": "first Apply clicked",
    "jobserve_final_apply_clicked": "clicking final Apply",
    "jobserve_submitted_message_seen": "submitted message seen",
    "jobserve_registration_toggle_disabled": "registration toggle disabled",
    "jobserve_modal_closed": "modal closed",
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the JobServe apply flow in a visible local Playwright browser.")
    parser.add_argument("--email", required=True, help="Email address to fill in the JobServe application modal.")
    parser.add_argument("--cv-path", help="Local CV file path to upload. If omitted, a remembered or discovered CV can be used.")
    parser.add_argument("--cv-search", action="store_true", help="Search common folders for a CV and choose from the matches.")
    parser.add_argument("--remember-cv-path", action="store_true", help="Remember the resolved CV path for future local debug runs.")
    parser.add_argument("--keywords", default="AI")
    parser.add_argument("--location", default="London")
    parser.add_argument("--distance", default="Within 50 miles")
    parser.add_argument("--posted", default="Within 7 days")
    parser.add_argument("--job-type", default="Any")
    parser.add_argument("--target-title")
    parser.add_argument("--target-company")
    parser.add_argument("--target-reference")
    parser.add_argument("--intended-title", help="Intended JobServe job title for local submit safety checks.")
    parser.add_argument("--intended-company", help="Intended JobServe company for local submit safety checks.")
    parser.add_argument("--intended-reference", help="Intended JobServe reference/external id for local submit safety checks.")
    parser.add_argument("--intended-url", help="Intended JobServe URL for local submit safety checks.")
    parser.add_argument("--use-current-selected-job", action="store_true", help="Local-only: use the currently selected JobServe result as the intended job after search results load.")
    parser.add_argument("--submit", action="store_true", help="Click the final JobServe Apply button. Defaults to review-only.")
    parser.add_argument("--auto-submit", action="store_true", help="Alias for --submit.")
    parser.add_argument("--pause-each-step", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--slow-mo-ms", type=int, default=500)
    parser.add_argument("--devtools", action="store_true")
    return parser.parse_args(argv)


class CVPathError(RuntimeError):
    pass


class LocalSubmitIdentityError(RuntimeError):
    pass


def resolve_cv_path(args: argparse.Namespace, *, input_func: Callable[[str], str] = input, search_dirs: list[Path] | None = None, remember_path: Path = REMEMBERED_CV_PATH) -> Path:
    provided_path = Path(args.cv_path).expanduser().resolve() if args.cv_path else None
    if provided_path and provided_path.exists() and not args.cv_search:
        _remember_cv_path_if_requested(args, provided_path, remember_path)
        args.cv_path = str(provided_path)
        return provided_path

    if provided_path and not provided_path.exists():
        _print_missing_cv_path_diagnostics(provided_path)

    if not provided_path and not args.cv_search:
        remembered = _load_remembered_cv_path(remember_path)
        if remembered and remembered.exists():
            print(f"[jobserve-debug] Using remembered CV: {remembered}", flush=True)
            args.cv_path = str(remembered)
            return remembered
        if remembered:
            print(f"[jobserve-debug] Remembered CV path is invalid, falling back to search: {remembered}", flush=True)

    candidates = discover_cv_files(search_dirs=search_dirs)
    if len(candidates) == 1:
        selected = candidates[0]
        print(f"[jobserve-debug] Using discovered CV: {selected}", flush=True)
        _remember_cv_path_if_requested(args, selected, remember_path)
        args.cv_path = str(selected)
        return selected
    if len(candidates) > 1:
        selected = choose_cv_file(candidates, input_func=input_func)
        _remember_cv_path_if_requested(args, selected, remember_path)
        args.cv_path = str(selected)
        return selected

    raise CVPathError("No likely CV file found. Pass --cv-path, place a CV in Downloads/Documents/Desktop, or run with --cv-search.")


def _print_missing_cv_path_diagnostics(path: Path) -> None:
    print(f"[jobserve-debug] CV file not found at: {path}", flush=True)
    print(f"[jobserve-debug] current working directory: {Path.cwd()}", flush=True)
    print(f"[jobserve-debug] parent folder exists: {path.parent.exists()}", flush=True)


def _load_remembered_cv_path(remember_path: Path = REMEMBERED_CV_PATH) -> Path | None:
    try:
        value = remember_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    return Path(value).expanduser().resolve() if value else None


def _remember_cv_path_if_requested(args: argparse.Namespace, cv_path: Path, remember_path: Path = REMEMBERED_CV_PATH) -> None:
    if not args.remember_cv_path:
        return
    remember_path.parent.mkdir(parents=True, exist_ok=True)
    remember_path.write_text(str(cv_path), encoding="utf-8")
    print(f"[jobserve-debug] Remembered CV path: {cv_path}", flush=True)


def cv_search_dirs() -> list[Path]:
    home = Path.home()
    explicit_home = Path(r"C:\Users\Home")
    return _dedupe_paths(
        [
            REPO_ROOT,
            Path.cwd(),
            home / "Downloads",
            home / "Documents",
            home / "Desktop",
            explicit_home / "Downloads",
            explicit_home / "Documents",
            explicit_home / "Desktop",
            explicit_home / "Web_Job_Scrap",
        ]
    )


def discover_cv_files(*, search_dirs: list[Path] | None = None) -> list[Path]:
    matches: list[tuple[int, float, Path]] = []
    for directory in cv_search_dirs() if search_dirs is None else search_dirs:
        if not directory.exists() or not directory.is_dir():
            continue
        for path in _iter_candidate_documents(directory):
            score = _cv_match_score(path)
            if score <= 0:
                continue
            try:
                mtime = path.stat().st_mtime
            except OSError:
                mtime = 0.0
            matches.append((score, mtime, path.resolve()))
    seen: set[Path] = set()
    ordered: list[Path] = []
    for _, _, path in sorted(matches, key=lambda item: (item[0], item[1], str(item[2]).lower()), reverse=True):
        if path in seen:
            continue
        seen.add(path)
        ordered.append(path)
    return ordered


def _iter_candidate_documents(directory: Path):
    try:
        iterator = directory.rglob("*")
        for path in iterator:
            if path.is_file() and path.suffix.lower() in CV_EXTENSIONS:
                yield path
    except OSError:
        return


def _cv_match_score(path: Path) -> int:
    name = path.name.lower()
    if path.suffix.lower() not in CV_EXTENSIONS:
        return 0
    if name.startswith("~$"):
        return 0
    score = 0
    for keyword in CV_KEYWORDS:
        if keyword in name:
            score += 5
    return score


def choose_cv_file(candidates: list[Path], *, input_func: Callable[[str], str] = input) -> Path:
    print("[jobserve-debug] Multiple likely CV files found:", flush=True)
    for index, path in enumerate(candidates, start=1):
        print(f"[jobserve-debug] {index}. {path}", flush=True)
    while True:
        choice = input_func("Choose CV number: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(candidates):
            selected = candidates[int(choice) - 1]
            print(f"[jobserve-debug] Using discovered CV: {selected}", flush=True)
            return selected
        print("[jobserve-debug] Invalid selection. Enter one of the listed numbers.", flush=True)


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    seen: set[Path] = set()
    result: list[Path] = []
    for path in paths:
        resolved = path.expanduser().resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        result.append(resolved)
    return result


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
    title = args.intended_title or args.target_title
    company = args.intended_company or args.target_company
    reference = args.intended_reference or args.target_reference
    identity_source = "current_selected_job" if args.use_current_selected_job else ("manual_args" if any([title, company, reference, args.intended_url]) else "missing")
    return {
        "job_id": None,
        "title": title,
        "original_title": title,
        "company_name": company,
        "original_company": company,
        "source_job_id": reference,
        "original_external_id": reference,
        "canonical_url": args.intended_url,
        "identity_source": identity_source,
    }


def run_shared_jobserve_flow(page, browser, args: argparse.Namespace, progress_callback: Callable[[str, dict[str, Any]], None] | None = None) -> AssistApplyResult:
    profile = build_local_profile(args)
    user = SimpleNamespace(email=args.email)
    candidates = apply_agent.profile_field_candidates(user, profile)
    mode = mode_from_args(args)
    return apply_agent._run_jobserve_search_to_apply(
        page,
        browser,
        candidates,
        profile,
        build_job_context(args),
        mode=mode,
        keep_open_for_review=not submit_requested(args),
        debug_mode=True,
        profile_diagnostics=apply_agent.profile_debug_payload(user, profile, candidates),
        progress_callback=progress_callback,
        use_current_selected_job_as_intended=args.use_current_selected_job,
    )


def submit_requested(args: argparse.Namespace) -> bool:
    return bool(args.submit or args.auto_submit)


def mode_from_args(args: argparse.Namespace) -> str:
    return "submit_with_confirmation" if submit_requested(args) else "review_only"


def validate_local_submit_identity(args: argparse.Namespace) -> None:
    if not submit_requested(args):
        return
    context = build_job_context(args)
    has_manual_identity = bool(any(str(context.get(key) or "").strip() for key in ["title", "company_name", "source_job_id", "canonical_url"]))
    if has_manual_identity or args.use_current_selected_job:
        return
    raise LocalSubmitIdentityError("Submit mode requires intended job identity. Pass --intended-title/--intended-company or --use-current-selected-job.")


class TerminalProgress:
    def __init__(self, *, pause_each_step: bool, submit_enabled: bool = False) -> None:
        self.pause_each_step = pause_each_step
        self.submit_enabled = submit_enabled
        self.seen: set[str] = set()

    def __call__(self, step: str, payload: dict[str, Any]) -> None:
        message = STEP_MESSAGES.get(step, step.replace("_", " "))
        suffix = ""
        if "succeeded" in payload:
            suffix = " ok" if payload.get("succeeded") else " failed"
        if step == "jobserve_search_remote_only_unchecked" and payload.get("result") == "already_unchecked":
            message = "remote only already unchecked"
            suffix = ""
        elif step == "jobserve_application_form_filled" and self.submit_enabled:
            message = "application form filled; running final submit safety checks"
        elif step == "jobserve_about_to_submit":
            guard = payload.get("submit_guard") or {}
            intended = guard.get("intended_job") or {}
            verified = guard.get("verified_job") or {}
            modal = guard.get("modal_job") or {}
            print("[jobserve-debug] SUBMIT MODE ENABLED. About to submit this JobServe application.", flush=True)
            print(f"[jobserve-debug] intended job: {intended.get('title') or intended.get('original_title')} / {intended.get('company_name') or intended.get('original_company')}", flush=True)
            print(f"[jobserve-debug] selected/detail job: {verified.get('title')} / {verified.get('company')}", flush=True)
            print(f"[jobserve-debug] modal job: {modal.get('title')} / {modal.get('company')}", flush=True)
            print(f"[jobserve-debug] email value: {guard.get('email_value') or '(missing)'}", flush=True)
            print(f"[jobserve-debug] CV attachment detected: {'yes' if guard.get('cv_uploaded') else 'no'}", flush=True)
            print(f"[jobserve-debug] working status selected: {guard.get('working_status_value') or guard.get('working_status_selected')}", flush=True)
            print(f"[jobserve-debug] identity verification result: {guard.get('identity_verified')}", flush=True)
            print(f"[jobserve-debug] final apply click enabled: {guard.get('final_apply_click_enabled')}", flush=True)
            suffix = (
                f" intended={guard.get('intended_job')} verified={guard.get('verified_job')} modal={guard.get('modal_job')} "
                f"email={guard.get('email_filled')} cv={guard.get('cv_uploaded')} status={guard.get('working_status_selected')}"
            )
        elif "jobserve_flow_diagnostics" in payload:
            flow = payload["jobserve_flow_diagnostics"]
            selected = flow.get("selected_job") if isinstance(flow, dict) else None
            if step == "jobserve_target_job_selected":
                auto = "matched" if flow.get("auto_selected_matched") else "did not match"
                replacement = flow.get("selected_result_identity") or selected
                suffix = f" auto-selected {auto}; selected={replacement}"
            elif step == "jobserve_results_loaded":
                suffix = " checking intended job identity"
            elif selected:
                suffix = f" selected={selected.get('title') or selected.get('text') or selected.get('href')}"
        print(f"[jobserve-debug] {message}{suffix}", flush=True)
        if self.pause_each_step and self.submit_enabled and step == "jobserve_application_form_filled":
            return
        if self.pause_each_step and self.submit_enabled and step == "jobserve_about_to_submit" and step not in self.seen:
            self.seen.add(step)
            input("Ready to submit verified JobServe application. Press Enter to submit.")
            return
        if self.pause_each_step and step not in self.seen:
            self.seen.add(step)
            input("Press Enter to continue...")


def run_visible_browser(args: argparse.Namespace) -> AssistApplyResult:
    validate_local_submit_identity(args)
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    TRACE_DIR.mkdir(parents=True, exist_ok=True)
    cv_path = resolve_cv_path(args)
    trace_path = TRACE_DIR / f"jobserve-visible-{int(time.time())}.zip"

    print_startup_config(args, trace_path, cv_path)

    from playwright.sync_api import sync_playwright

    result: AssistApplyResult | None = None
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False, slow_mo=args.slow_mo_ms, devtools=args.devtools)
        context = browser.new_context(record_video_dir=str(VIDEO_DIR), viewport={"width": 1440, "height": 1000})
        context.tracing.start(screenshots=True, snapshots=True, sources=True)
        page = context.new_page()
        try:
            result = run_shared_jobserve_flow(page, browser, args, TerminalProgress(pause_each_step=args.pause_each_step, submit_enabled=submit_requested(args)))
            if result.submitted:
                print("[jobserve-debug] submitted successfully", flush=True)
                print(f"[jobserve-debug] confirmation text: {result.confirmation_text or '(not captured)'}", flush=True)
                print(f"[jobserve-debug] registration toggle disabled: {result.registration_toggle_disabled}", flush=True)
                print(f"[jobserve-debug] modal closed: {result.modal_closed}", flush=True)
            elif submit_requested(args):
                reason = None
                if isinstance(result.jobserve_flow_diagnostics, dict):
                    reason = result.jobserve_flow_diagnostics.get("blocked_reason")
                warnings = "; ".join(result.warnings or [])
                print(f"[jobserve-debug] submit mode did not submit; status={result.status}; reason={reason or warnings or 'unknown'}", flush=True)
            else:
                print("[jobserve-debug] ready to submit; review-only mode did not click final Apply", flush=True)
            input("Press Enter to close the visible browser...")
            return result
        except Exception:
            print("[jobserve-debug] flow failed; browser is being kept open for inspection", flush=True)
            try:
                page.screenshot(path=str(DEBUG_ROOT / "search-failure-before-close.jpg"), full_page=False, type="jpeg", quality=80)
                print(f"[jobserve-debug] failure screenshot: {DEBUG_ROOT / 'search-failure-before-close.jpg'}", flush=True)
            except Exception:
                pass
            input("Press Enter to close the visible browser...")
            raise
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


def print_startup_config(args: argparse.Namespace, trace_path: Path, cv_path: Path | None = None) -> None:
    submit_enabled = submit_requested(args)
    resolved_cv_path = cv_path or (Path(args.cv_path).expanduser().resolve() if args.cv_path else None)
    job_context = build_job_context(args)
    print("[jobserve-debug] opening search page", flush=True)
    print(f"[jobserve-debug] submit flag received: {str(submit_enabled).lower()}", flush=True)
    print(f"[jobserve-debug] mode selected: {mode_from_args(args)}", flush=True)
    print(f"[jobserve-debug] identity source: {job_context.get('identity_source')}", flush=True)
    print(f"[jobserve-debug] intended title: {job_context.get('title') or '(missing)'}", flush=True)
    print(f"[jobserve-debug] intended company: {job_context.get('company_name') or '(missing)'}", flush=True)
    print(f"[jobserve-debug] intended reference: {job_context.get('source_job_id') or '(missing)'}", flush=True)
    print(f"[jobserve-debug] email: {args.email}", flush=True)
    print(f"[jobserve-debug] resolved CV path: {resolved_cv_path or '(none)'}", flush=True)
    print(f"[jobserve-debug] CV exists/readable: {str(_is_readable_file(resolved_cv_path)).lower()}", flush=True)
    print(f"[jobserve-debug] CV size: {_file_size_label(resolved_cv_path)}", flush=True)
    print(f"[jobserve-debug] final apply click enabled: {str(submit_enabled).lower()}", flush=True)
    if submit_enabled:
        print("[jobserve-debug] submit mode enabled: final JobServe Apply will be clicked only after safety checks pass", flush=True)
    else:
        print("[jobserve-debug] review-only mode selected: pass --submit or --auto-submit to click final Apply", flush=True)
    print(f"[jobserve-debug] video directory: {VIDEO_DIR}", flush=True)
    print(f"[jobserve-debug] trace path: {trace_path}", flush=True)


def _is_readable_file(path: Path | None) -> bool:
    if not path or not path.exists() or not path.is_file():
        return False
    try:
        with path.open("rb"):
            return True
    except OSError:
        return False


def _file_size_label(path: Path | None) -> str:
    if not path or not path.exists():
        return "(missing)"
    try:
        return str(path.stat().st_size)
    except OSError:
        return "(unreadable)"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        run_visible_browser(args)
        return 0
    except (CVPathError, LocalSubmitIdentityError) as exc:
        print(f"[jobserve-debug] {exc}", flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
