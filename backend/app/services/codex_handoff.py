from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from typing import Any
from urllib.parse import urlparse

import requests

from app.config import settings

GITHUB_API_BASE = "https://api.github.com"
HANDOFF_LABELS = ["codex", "autonomous-canary", "assist-apply"]
ATTEMPT_MARKER = "<!-- autonomous-handoff-attempt -->"
VERIFICATION_MARKER = "<!-- autonomous-verification-passed -->"

SECRET_KEY_PATTERN = re.compile(r"(secret|token|key|password|authorization|cookie|database_url|redis_url|deploy_hook|github_token)", re.I)
EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
BEARER_PATTERN = re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+", re.I)


def create_or_update_codex_handoff(report: dict[str, Any]) -> dict[str, Any]:
    safe_report = redact(report)
    if _is_success_report(safe_report):
        return comment_and_close_verified_issues(safe_report)
    if not settings.github_token or not settings.github_repository:
        return {"status": "failed", "error": "github_handoff_not_configured", "issue_url": None}
    try:
        title = _issue_title(safe_report)
        body = _issue_body(safe_report)
        existing = _find_open_issue(title)
        if existing:
            guard = _loop_guard(existing, safe_report)
            if guard:
                return guard
            comment = f"{ATTEMPT_MARKER}\n\n{body}"
            _github_request("POST", f"/repos/{settings.github_repository}/issues/{existing['number']}/comments", json={"body": comment})
            return {"status": "updated", "issue_url": existing.get("html_url"), "issue_number": existing.get("number")}

        issue = _github_request(
            "POST",
            f"/repos/{settings.github_repository}/issues",
            json={"title": title, "body": f"{ATTEMPT_MARKER}\n\n{body}", "labels": HANDOFF_LABELS},
        )
        return {"status": "created", "issue_url": issue.get("html_url"), "issue_number": issue.get("number")}
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "error": f"github_handoff_error: {exc}", "issue_url": None}


def comment_and_close_verified_issues(report: dict[str, Any]) -> dict[str, Any]:
    if not settings.github_token or not settings.github_repository:
        return {"status": "failed", "error": "github_handoff_not_configured", "issue_url": None}
    try:
        app_id = report.get("application_id")
        if app_id is None:
            return {"status": "not_found", "issue_url": None}
        issues = _open_handoff_issues()
        matched = [issue for issue in issues if f"application {app_id}" in str(issue.get("title", "")).lower()]
        if not matched:
            return {"status": "not_found", "issue_url": None}
        closed: list[str] = []
        for issue in matched:
            number = issue["number"]
            body = "\n".join(
                [
                    VERIFICATION_MARKER,
                    "",
                    "Post-deploy autonomous verification passed.",
                    f"application_id: {app_id if app_id is not None else 'not specified'}",
                    f"latest_commit_sha: {report.get('latest_commit_sha') or report.get('code_revision') or 'unknown'}",
                    f"verified_at: {_now()}",
                ]
            )
            _github_request("POST", f"/repos/{settings.github_repository}/issues/{number}/comments", json={"body": body})
            _github_request("PATCH", f"/repos/{settings.github_repository}/issues/{number}", json={"state": "closed"})
            if issue.get("html_url"):
                closed.append(issue["html_url"])
        return {"status": "closed", "issue_url": closed[0] if closed else None, "closed_issue_urls": closed}
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "error": f"github_handoff_error: {exc}", "issue_url": None}


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if SECRET_KEY_PATTERN.search(str(key)):
                redacted[key] = "[redacted]"
            else:
                redacted[key] = redact(item)
        return redacted
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return _redact_string(value)
    return value


def environment_summary() -> dict[str, Any]:
    return {
        "app_env": settings.app_env,
        "service_type": settings.service_type,
        "queue_enabled": settings.queue_enabled,
        "queue_name": settings.queue_name,
        "database_host": _host(settings.database_url),
        "redis_host": _host(settings.redis_url),
        "autonomous_real_submit_enabled": settings.autonomous_real_submit_enabled,
        "max_autonomous_real_submits_per_run": settings.max_autonomous_real_submits_per_run,
    }


def _find_open_issue(title: str) -> dict[str, Any] | None:
    for issue in _open_handoff_issues():
        if issue.get("title") == title:
            return issue
    return None


def _open_handoff_issues() -> list[dict[str, Any]]:
    return _github_request(
        "GET",
        f"/repos/{settings.github_repository}/issues",
        params={"state": "open", "labels": ",".join(["autonomous-canary", "assist-apply"]), "per_page": 100},
    )


def _loop_guard(issue: dict[str, Any], report: dict[str, Any]) -> dict[str, Any] | None:
    comments = _github_request("GET", f"/repos/{settings.github_repository}/issues/{issue['number']}/comments", params={"per_page": 100})
    bodies = [str(issue.get("body") or ""), *[str(comment.get("body") or "") for comment in comments]]
    attempts = sum(body.count(ATTEMPT_MARKER) for body in bodies)
    exact_error = str(report.get("exact_error") or "")
    repeated_errors = sum(1 for body in bodies if exact_error and exact_error in body)
    if attempts >= settings.max_autonomous_fix_attempts_per_issue:
        return {
            "status": "loop_guard_stopped",
            "error": "max_autonomous_fix_attempts_reached",
            "issue_url": issue.get("html_url"),
            "issue_number": issue.get("number"),
        }
    if repeated_errors >= 2:
        return {
            "status": "loop_guard_stopped",
            "error": "same_exact_error_repeated_after_two_deploys",
            "issue_url": issue.get("html_url"),
            "issue_number": issue.get("number"),
        }
    return None


def _issue_title(report: dict[str, Any]) -> str:
    app_id = report.get("application_id") or "unknown"
    phase = _one_line(report.get("failed_phase") or "unknown_failure", 80)
    return f"Autonomous canary failure for application {app_id}: {phase}"


def _issue_body(report: dict[str, Any]) -> str:
    lines = [
        f"{settings.codex_mention} fix this failure",
        "",
        f"failed_phase: {report.get('failed_phase') or 'unknown'}",
        f"exact_error: {report.get('exact_error') or 'unknown'}",
        f"recommended_fix: {report.get('recommended_fix') or 'Inspect autonomous canary artifacts and diagnostics.'}",
        f"application_id: {report.get('application_id') or 'unknown'}",
        f"job_title: {report.get('job_title') or 'unknown'}",
        f"job_company: {report.get('job_company') or 'unknown'}",
        f"latest_commit_sha: {report.get('latest_commit_sha') or report.get('code_revision') or 'unknown'}",
        "",
        "environment_summary:",
        _json_block(report.get("environment_summary") or {}),
        "",
        "artifact_paths_or_links:",
        _json_block(report.get("artifact_links") or report.get("artifact_paths") or []),
        "",
        "orchestration_steps:",
        _json_block(report.get("orchestration_steps") or []),
    ]
    if report.get("traceback"):
        lines.extend(["", "traceback:", "```", _one_line(str(report["traceback"]), 6000), "```"])
    return "\n".join(lines)


def _github_request(method: str, path: str, **kwargs: Any) -> Any:
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {settings.github_token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    response = requests.request(method, f"{GITHUB_API_BASE}{path}", headers=headers, timeout=20, **kwargs)
    response.raise_for_status()
    if response.content:
        return response.json()
    return {}


def _is_success_report(report: dict[str, Any]) -> bool:
    return bool(
        report.get("verification_passed") is True
        or report.get("overall_status") == "ok"
        or (report.get("status") in {"passed", "submitted"} and not report.get("failed_phase"))
    )


def _host(value: str) -> str:
    try:
        return urlparse(value).hostname or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def _one_line(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text[:limit]


def _redact_string(value: str) -> str:
    text = EMAIL_PATTERN.sub("[redacted-email]", value)
    text = BEARER_PATTERN.sub("Bearer [redacted]", text)
    if "://" in text:
        parsed = urlparse(text)
        if parsed.password or parsed.username:
            safe_netloc = parsed.hostname or "redacted-host"
            if parsed.port:
                safe_netloc = f"{safe_netloc}:{parsed.port}"
            text = parsed._replace(netloc=safe_netloc).geturl()
    return text


def _json_block(value: Any) -> str:
    return "```json\n" + json.dumps(value, indent=2, sort_keys=True, default=str)[:6000] + "\n```"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
