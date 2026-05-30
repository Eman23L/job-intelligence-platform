from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import time
import traceback
from typing import Any, Callable

REPORT_DIR = Path("backend/runtime/assist_apply_diagnostics")
LATEST_JSON = "latest_assist_apply_probe.json"
LATEST_MD = "latest_assist_apply_probe.md"
SECRET_KEY_PATTERN = re.compile(r"(SECRET|TOKEN|KEY|PASSWORD|HOOK|DATABASE_URL|REDIS_URL)", re.I)


@dataclass
class PhaseResult:
    status: str = "not_run"
    error: str | None = None
    traceback: str | None = None
    data: dict[str, Any] | None = None


def redact_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return {key: ("[redacted]" if SECRET_KEY_PATTERN.search(str(key)) else redact_value(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, str):
        if "://" in value and ("@" in value or value.startswith(("postgres", "redis", "http"))):
            return "[redacted]"
    return value


def env_summary() -> dict[str, Any]:
    keys = [
        "APP_ENV",
        "SERVICE_TYPE",
        "QUEUE_ENABLED",
        "QUEUE_NAME",
        "PLAYWRIGHT_ENABLED",
        "PLAYWRIGHT_BROWSERS_PATH",
        "PYTHON_VERSION",
        "DATABASE_URL",
        "REDIS_URL",
        "GITHUB_TOKEN",
        "RENDER_WEB_DEPLOY_HOOK_URL",
        "RENDER_WORKER_DEPLOY_HOOK_URL",
    ]
    return redact_value({key: os.environ.get(key) for key in keys})


def bootstrap_summary(command_args: list[str] | None = None) -> dict[str, Any]:
    return {
        "command_args": command_args if command_args is not None else list(os.sys.argv[1:]),
        "cwd": str(Path.cwd()),
        "python_version": os.sys.version,
        "pythonpath": os.environ.get("PYTHONPATH"),
        "backend_app_exists": Path("backend/app").exists(),
        "probe_module_imported": True,
    }


def recommended_fix_for_phase(phase: str, error: str | None) -> str:
    if phase == "probe_argument_error":
        return "Pass a valid --application-id and rerun the diagnostics workflow."
    if phase == "probe_import_failed":
        return "Fix the probe startup import/config error. Ensure required environment variables are present before importing app DB modules."
    if phase == "probe_missing_configuration":
        return "Set DATABASE_URL and REDIS_URL repository secrets for the diagnostics workflow, then rerun it."
    if phase == "redis_queue":
        return "Verify REDIS_URL, QUEUE_NAME, and that the Render web and worker services point at the same Redis instance."
    if phase == "db_lookup":
        return "Verify DATABASE_URL and that the application, user, profile, and CV records exist and are readable."
    if phase == "jobserve_url_resolution":
        return "Check the saved JobServe canonical/apply URL and source identifiers for this application."
    if phase == "browser_launch":
        return "Verify Playwright is enabled and Chromium is installed in the runtime image."
    if phase in {"jobserve_navigation", "modal_form_detection"}:
        return "Inspect screenshot/html artifacts and update the JobServe selectors or flow handling."
    if error:
        return "Inspect the captured traceback and fix the failing probe phase."
    return "No fix required."


def github_handoff_payload(report: dict[str, Any], artifact_paths: list[str]) -> dict[str, Any]:
    title = f"Assist apply diagnostics failed for application {report.get('application_id')}"
    body = "\n".join(
        [
            "@codex fix this failure",
            "",
            f"failed_phase: {report.get('failed_phase') or 'unknown'}",
            f"exact_error: {report.get('exact_error') or 'unknown'}",
            f"traceback: {report.get('traceback') or 'not captured'}",
            f"recommended_fix: {report.get('recommended_fix') or 'Inspect diagnostic artifacts.'}",
            f"artifact paths: {', '.join(artifact_paths) if artifact_paths else 'none'}",
        ]
    )
    return {"title": title, "body": body, "labels": ["assist-apply-diagnostics", "codex"]}


def base_report(
    application_id: int | None,
    user_id: int | None,
    *,
    safe_mode: bool,
    submit_allowed: bool,
    command_args: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "overall_status": "ok",
        "application_id": application_id,
        "user_id": user_id,
        "safe_mode": safe_mode,
        "submit_allowed": submit_allowed,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "failed_phase": None,
        "exact_error": None,
        "traceback": None,
        "recommended_fix": None,
        "env_summary": env_summary(),
        "bootstrap": bootstrap_summary(command_args),
        "phases": {},
        "timings": {},
        "artifact_paths": [],
    }


def failure_report(
    application_id: int | None,
    user_id: int | None,
    *,
    safe_mode: bool,
    submit_allowed: bool = False,
    failed_phase: str = "probe_startup",
    exact_error: str,
    traceback_text: str,
    command_args: list[str] | None = None,
) -> dict[str, Any]:
    report = base_report(application_id, user_id, safe_mode=safe_mode, submit_allowed=submit_allowed, command_args=command_args)
    report.update(
        {
            "overall_status": "failed",
            "failed_phase": failed_phase,
            "exact_error": exact_error,
            "traceback": traceback_text,
            "recommended_fix": recommended_fix_for_phase(failed_phase, exact_error),
        }
    )
    report["phases"][failed_phase] = {"status": "failed", "error": exact_error, "traceback": traceback_text}
    return report


def write_report_files(report: dict[str, Any], output_dir: Path = REPORT_DIR, *, timestamped: bool = True) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    application_id = report.get("application_id") or "unknown"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    latest_json = output_dir / LATEST_JSON
    latest_md = output_dir / LATEST_MD
    artifact_paths = [str(latest_json), str(latest_md)]
    if timestamped:
        artifact_paths.extend(
            [
                str(output_dir / f"assist_apply_probe_{application_id}_{timestamp}.json"),
                str(output_dir / f"assist_apply_probe_{application_id}_{timestamp}.md"),
            ]
        )
    existing_artifacts = [path for path in report.get("artifact_paths", []) if path not in artifact_paths]
    report["artifact_paths"] = [*existing_artifacts, *artifact_paths]
    report["github_handoff"] = github_handoff_payload(report, report["artifact_paths"])
    sanitized = redact_value(report)
    latest_json.write_text(json.dumps(sanitized, indent=2, sort_keys=True), encoding="utf-8")
    latest_md.write_text(markdown_report(sanitized), encoding="utf-8")
    if timestamped:
        Path(artifact_paths[2]).write_text(json.dumps(sanitized, indent=2, sort_keys=True), encoding="utf-8")
        Path(artifact_paths[3]).write_text(markdown_report(sanitized), encoding="utf-8")
    return sanitized


def _phase(report: dict[str, Any], name: str, action: Callable[[], dict[str, Any] | None]) -> None:
    started = time.perf_counter()
    try:
        data = action() or {}
        report["phases"][name] = {"status": "ok", "data": redact_value(data)}
    except Exception as exc:  # noqa: BLE001
        report["phases"][name] = {"status": "failed", "error": str(exc), "traceback": traceback.format_exc()}
        if report["overall_status"] == "ok":
            report["overall_status"] = "failed"
            report["failed_phase"] = name
            report["exact_error"] = str(exc)
            report["traceback"] = traceback.format_exc()
            report["recommended_fix"] = recommended_fix_for_phase(name, str(exc))
    finally:
        report["timings"][name] = int((time.perf_counter() - started) * 1000)


def import_runtime_dependencies() -> dict[str, Any]:
    from sqlalchemy import select

    from app.config import settings
    from app.db.models import Job, JobScore, User
    from app.db.session import SessionLocal
    from app.services import apply_agent
    from app.services.browser_automation import browser_status, chromium_executable_path
    from app.services.profile import get_profile
    from app.services.queue import redis_connection, redis_url_host

    return {
        "select": select,
        "settings": settings,
        "Job": Job,
        "JobScore": JobScore,
        "User": User,
        "SessionLocal": SessionLocal,
        "apply_agent": apply_agent,
        "browser_status": browser_status,
        "chromium_executable_path": chromium_executable_path,
        "get_profile": get_profile,
        "redis_connection": redis_connection,
        "redis_url_host": redis_url_host,
    }


def validate_required_configuration() -> dict[str, Any]:
    missing = [key for key in ("DATABASE_URL", "REDIS_URL") if not os.environ.get(key)]
    if missing:
        raise RuntimeError(f"missing required configuration: {', '.join(missing)}")
    return {"database_url_present": True, "redis_url_present": True}


def build_report(
    application_id: int,
    user_id: int,
    *,
    safe_mode: bool = True,
    submit_allowed: bool = False,
    output_dir: Path = REPORT_DIR,
    run_browser_navigation: bool = True,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report: dict[str, Any] = base_report(application_id, user_id, safe_mode=safe_mode, submit_allowed=submit_allowed)

    context: dict[str, Any] = {"job": None, "user": None, "profile": None, "resolved_url": None}

    _phase(report, "probe_missing_configuration", validate_required_configuration)
    if report["overall_status"] != "ok":
        return write_report_files(report, output_dir)

    deps: dict[str, Any] = {}

    def import_dependencies() -> dict[str, Any]:
        deps.update(import_runtime_dependencies())
        return {"imported": sorted(deps.keys())}

    _phase(report, "probe_import_failed", import_dependencies)

    if not deps:
        return write_report_files(report, output_dir)

    settings = deps["settings"]
    select = deps["select"]
    Job = deps["Job"]
    JobScore = deps["JobScore"]
    User = deps["User"]
    SessionLocal = deps["SessionLocal"]
    apply_agent = deps["apply_agent"]
    browser_status = deps["browser_status"]
    chromium_executable_path = deps["chromium_executable_path"]
    get_profile = deps["get_profile"]
    redis_connection = deps["redis_connection"]
    redis_url_host = deps["redis_url_host"]

    _phase(report, "redis_queue", lambda: {"redis_host": redis_url_host(), "ping": bool(redis_connection().ping()), "queue_name": settings.queue_name})

    def db_lookup() -> dict[str, Any]:
        with SessionLocal() as db:
            job = db.get(Job, application_id)
            user = db.get(User, user_id)
            profile = get_profile(db, user) if user is not None else None
            score = db.scalar(select(JobScore).where(JobScore.job_id == application_id, JobScore.user_id == user_id).order_by(JobScore.scored_at.desc()))
            context.update({"job": job, "user": user, "profile": profile})
            if job is None:
                raise ValueError("application_not_found")
            return {
                "application_found": job is not None,
                "user_found": user is not None,
                "profile_found": profile is not None,
                "cv_found": bool(profile and (getattr(profile, "cv_file_path", None) or getattr(profile, "cv_file_bytes", None))),
                "job_title": getattr(job, "title", None),
                "job_company": getattr(job, "company_name", None),
                "score_found": score is not None,
            }

    _phase(report, "db_lookup", db_lookup)

    def resolve_url() -> dict[str, Any]:
        job = context.get("job")
        if job is None:
            raise ValueError("application_not_loaded")
        diagnostics = apply_agent._resolve_assist_apply_url_diagnostics(job)
        resolved = apply_agent._resolve_assist_apply_url(job)
        context["resolved_url"] = resolved
        return {"resolved_url": resolved, "diagnostics": diagnostics}

    _phase(report, "jobserve_url_resolution", resolve_url)

    _phase(report, "browser_launch", lambda: {"browser_status": browser_status(), "chromium_executable_path": chromium_executable_path()})

    if not safe_mode or submit_allowed:
        report["phases"]["submit_guard"] = {"status": "ok", "data": {"submit_allowed": submit_allowed}}
    else:
        report["phases"]["submit_guard"] = {"status": "ok", "data": {"safe_mode_prevents_final_submit": True}}

    def navigation_probe() -> dict[str, Any]:
        resolved_url = context.get("resolved_url")
        if not resolved_url:
            raise ValueError("jobserve_url_not_resolved")
        from playwright.sync_api import sync_playwright

        screenshot_path = output_dir / f"assist_apply_{application_id}_{timestamp}.png"
        html_path = output_dir / f"assist_apply_{application_id}_{timestamp}.html"
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.goto(resolved_url, wait_until="domcontentloaded", timeout=settings.page_navigation_timeout_ms)
                page.screenshot(path=str(screenshot_path), full_page=False)
                html_path.write_text(page.content()[:500_000], encoding="utf-8")
                apply_target = apply_agent._find_apply_target(page, browser)
                context_found = None
                if apply_target is not None:
                    apply_target.click(timeout=8000)
                    page.wait_for_timeout(1200)
                    context_found = apply_agent._find_jobserve_form_context(page, browser)
                else:
                    context_found = apply_agent._find_jobserve_form_context(page, browser)
                return {
                    "url": page.url,
                    "title": page.title(),
                    "apply_button_found": apply_target is not None,
                    "modal_or_form_found": context_found is not None,
                    "final_apply_clicked": False,
                    "screenshot_path": str(screenshot_path),
                    "html_path": str(html_path),
                }
            finally:
                browser.close()

    if run_browser_navigation:
        _phase(report, "jobserve_navigation", navigation_probe)
    else:
        report["phases"]["jobserve_navigation"] = {"status": "skipped", "data": {"reason": "run_browser_navigation=false"}}
        report["timings"]["jobserve_navigation"] = 0
    nav_data = (report["phases"].get("jobserve_navigation") or {}).get("data") or {}
    for key in ["screenshot_path", "html_path"]:
        if nav_data.get(key):
            report["artifact_paths"].append(nav_data[key])
    report["phases"]["modal_form_detection"] = {
        "status": "ok" if nav_data.get("apply_button_found") or nav_data.get("modal_or_form_found") else "not_confirmed",
        "data": {"safe_mode_no_final_apply_click": safe_mode and not submit_allowed},
    }

    if report["overall_status"] == "ok":
        report["recommended_fix"] = recommended_fix_for_phase("", None)

    return write_report_files(report, output_dir)


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        f"# Assist Apply Probe {report.get('application_id')}",
        "",
        f"- overall_status: {report.get('overall_status')}",
        f"- failed_phase: {report.get('failed_phase')}",
        f"- exact_error: {report.get('exact_error')}",
        f"- recommended_fix: {report.get('recommended_fix')}",
        f"- safe_mode: {report.get('safe_mode')}",
        "",
        "## Phases",
    ]
    for name, phase in (report.get("phases") or {}).items():
        lines.append(f"- {name}: {phase.get('status')}")
        if phase.get("error"):
            lines.append(f"  - error: {phase.get('error')}")
    lines.extend(["", "## Artifacts"])
    lines.extend(f"- {path}" for path in report.get("artifact_paths") or [])
    return "\n".join(lines) + "\n"


def parse_bool(value: str | bool) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"1", "true", "yes", "y"}


class ReportingArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(message)


def main() -> None:
    parser = ReportingArgumentParser()
    parser.add_argument("--application-id", type=int, required=True)
    parser.add_argument("--user-id", type=int, default=1)
    parser.add_argument("--safe-mode", default="true")
    parser.add_argument("--submit-allowed", default="false")
    try:
        args = parser.parse_args()
    except Exception as exc:  # noqa: BLE001
        report = write_report_files(
            failure_report(
                None,
                None,
                safe_mode=True,
                submit_allowed=False,
                failed_phase="probe_argument_error",
                exact_error=str(exc),
                traceback_text=traceback.format_exc(),
                command_args=list(os.sys.argv[1:]),
            )
        )
        print(json.dumps({"overall_status": report["overall_status"], "failed_phase": report["failed_phase"], "artifact_paths": report["artifact_paths"]}, sort_keys=True))
        raise SystemExit(2)
    safe_mode = parse_bool(args.safe_mode)
    submit_allowed = parse_bool(args.submit_allowed)
    try:
        report = build_report(args.application_id, args.user_id, safe_mode=safe_mode, submit_allowed=submit_allowed)
    except Exception as exc:  # noqa: BLE001
        report = write_report_files(
            failure_report(
                args.application_id,
                args.user_id,
                safe_mode=safe_mode,
                submit_allowed=submit_allowed,
                failed_phase="probe_unhandled_exception",
                exact_error=str(exc),
                traceback_text=traceback.format_exc(),
                command_args=list(os.sys.argv[1:]),
            )
        )
    print(json.dumps({"overall_status": report["overall_status"], "failed_phase": report["failed_phase"], "artifact_paths": report["artifact_paths"]}, sort_keys=True))
    if report["overall_status"] != "ok":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
