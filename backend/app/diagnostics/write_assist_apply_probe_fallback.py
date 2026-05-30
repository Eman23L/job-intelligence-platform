from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re

REPORT_DIR = Path("backend/runtime/assist_apply_diagnostics")
LATEST_JSON = "latest_assist_apply_probe.json"
LATEST_MD = "latest_assist_apply_probe.md"
SECRET_PATTERN = re.compile(r"(postgres(?:ql)?|redis|https?)://[^\s\"'<>]+", re.I)


def _redact(text: str) -> str:
    return SECRET_PATTERN.sub("[redacted]", text)


def _tail(path: Path, limit: int = 8000) -> str:
    if not path.exists():
        return ""
    return _redact(path.read_text(encoding="utf-8", errors="replace")[-limit:])


def build_fallback_report(application_id: str, safe_mode: str, root: Path = REPORT_DIR) -> dict[str, object]:
    root.mkdir(parents=True, exist_ok=True)
    stdout_tail = _tail(root / "probe_stdout.log")
    stderr_tail = _tail(root / "probe_stderr.log")
    artifact_paths = [
        str(root / LATEST_JSON),
        str(root / LATEST_MD),
        str(root / "probe_stdout.log"),
        str(root / "probe_stderr.log"),
    ]
    body = "\n".join(
        [
            "@codex fix this failure",
            "",
            "failed_phase: probe_no_report",
            "exact_error: assist_apply_probe exited before writing a report",
            "traceback: not captured",
            "recommended_fix: Inspect probe_stdout.log and probe_stderr.log, then fix probe startup/import errors.",
            "stdout_tail:",
            stdout_tail[-2000:] or "(empty)",
            "stderr_tail:",
            stderr_tail[-2000:] or "(empty)",
            f"artifact paths: {', '.join(artifact_paths)}",
        ]
    )
    return {
        "overall_status": "failed",
        "failed_phase": "probe_no_report",
        "exact_error": "assist_apply_probe exited before writing a report",
        "traceback": "not captured",
        "recommended_fix": "Inspect probe_stdout.log and probe_stderr.log, then fix probe startup/import errors.",
        "application_id": application_id,
        "safe_mode": safe_mode,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "stdout_tail": stdout_tail,
        "stderr_tail": stderr_tail,
        "artifact_paths": artifact_paths,
        "github_handoff": {
            "title": f"Assist apply diagnostics failed for application {application_id}",
            "body": body,
            "labels": ["assist-apply-diagnostics", "codex"],
        },
    }


def write_fallback_report(application_id: str, safe_mode: str, root: Path = REPORT_DIR) -> dict[str, object]:
    report = build_fallback_report(application_id, safe_mode, root)
    root.mkdir(parents=True, exist_ok=True)
    (root / LATEST_JSON).write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    (root / LATEST_MD).write_text(
        "\n".join(
            [
                "# Assist Apply Probe",
                "",
                "- overall_status: failed",
                "- failed_phase: probe_no_report",
                "- exact_error: assist_apply_probe exited before writing a report",
                "- recommended_fix: Inspect probe_stdout.log and probe_stderr.log, then fix probe startup/import errors.",
                "",
                "## probe_stdout.log tail",
                "```",
                str(report["stdout_tail"]) or "(empty)",
                "```",
                "",
                "## probe_stderr.log tail",
                "```",
                str(report["stderr_tail"]) or "(empty)",
                "```",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return report


def main() -> None:
    write_fallback_report(os.environ.get("APPLICATION_ID", "unknown"), os.environ.get("SAFE_MODE", "true"))


if __name__ == "__main__":
    main()
