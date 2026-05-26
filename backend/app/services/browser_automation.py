from __future__ import annotations

from dataclasses import dataclass
import importlib.util
import logging
import os
from pathlib import Path
from typing import Any

from app.config import settings
from app.services.queue import redis_connection

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BrowserAutomationAvailability:
    available: bool
    error: str | None = None
    message: str | None = None
    chromium_executable_path: str | None = None


@dataclass(frozen=True)
class ChromiumDetection:
    executable_path: str | None
    exists: bool
    executable: bool
    source: str | None = None


def browser_status() -> dict[str, Any]:
    redis_ok = redis_connected()
    chromium = detect_chromium()
    return {
        "queue_enabled": settings.queue_enabled,
        "redis_connected": redis_ok,
        "playwright_installed": playwright_installed(),
        "chromium_available": chromium.exists and chromium.executable,
        "worker_running": worker_running(redis_ok=redis_ok),
    }


def validate_browser_automation_availability(*, require_worker: bool = False) -> BrowserAutomationAvailability:
    if not settings.playwright_enabled:
        return BrowserAutomationAvailability(
            available=False,
            error="worker_unavailable",
            message="Browser automation worker is offline.",
        )
    if not playwright_installed():
        return BrowserAutomationAvailability(
            available=False,
            error="playwright_not_installed",
            message="Playwright is not installed in this environment.",
        )
    chromium = detect_chromium()
    if not (chromium.exists and chromium.executable):
        return BrowserAutomationAvailability(
            available=False,
            error="chromium_not_installed",
            message="Playwright Chromium is not installed in this environment.",
            chromium_executable_path=chromium.executable_path,
        )
    status = browser_status()
    if require_worker and settings.queue_enabled and not status["worker_running"]:
        return BrowserAutomationAvailability(
            available=False,
            error="worker_unavailable",
            message="Browser automation worker is offline.",
            chromium_executable_path=chromium.executable_path,
        )
    return BrowserAutomationAvailability(available=True, chromium_executable_path=chromium.executable_path)


def playwright_installed() -> bool:
    return importlib.util.find_spec("playwright") is not None


def chromium_executable_path() -> str | None:
    return detect_chromium().executable_path


def detect_chromium() -> ChromiumDetection:
    if not playwright_installed():
        return ChromiumDetection(executable_path=None, exists=False, executable=False)
    api_path = _playwright_chromium_executable_path()
    if api_path:
        detection = _detection_for_path(api_path, source="playwright_api")
        if detection.exists and detection.executable:
            return detection
    glob_path = _glob_chromium_executable_path()
    if glob_path:
        return _detection_for_path(str(glob_path), source="cache_glob")
    if api_path:
        return _detection_for_path(api_path, source="playwright_api")
    return ChromiumDetection(executable_path=None, exists=False, executable=False)


def chromium_diagnostics() -> dict[str, Any]:
    detection = detect_chromium()
    cache_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    return {
        "playwright_browsers_path": cache_path,
        "chromium_executable_path": detection.executable_path,
        "chromium_path_source": detection.source,
        "chromium_file_exists": detection.exists,
        "chromium_file_executable": detection.executable,
        "ms_playwright_listing": _browser_cache_listing(cache_path) if not (detection.exists and detection.executable) else [],
    }


def _playwright_chromium_executable_path() -> str | None:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            return playwright.chromium.executable_path
    except Exception as exc:  # noqa: BLE001
        logger.warning("playwright_chromium_path_check_failed error=%s", exc)
        return None


def _glob_chromium_executable_path() -> Path | None:
    cache_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if not cache_path:
        return None
    root = Path(cache_path)
    candidates = [
        *root.glob("chromium-*/chrome-linux/chrome"),
        *root.glob("chromium-*/chrome-win/chrome.exe"),
        *root.glob("chromium-*/chrome-mac/Chromium.app/Contents/MacOS/Chromium"),
    ]
    usable = [candidate for candidate in candidates if candidate.exists() and _is_executable(candidate)]
    if usable:
        return sorted(usable)[-1]
    if candidates:
        return sorted(candidates)[-1]
    return None


def _detection_for_path(path: str, *, source: str) -> ChromiumDetection:
    executable = Path(path)
    exists = executable.exists()
    return ChromiumDetection(
        executable_path=str(executable),
        exists=exists,
        executable=exists and _is_executable(executable),
        source=source,
    )


def _is_executable(path: Path) -> bool:
    return os.access(path, os.X_OK)


def _browser_cache_listing(cache_path: str | None) -> list[str]:
    if not cache_path:
        return []
    root = Path(cache_path)
    if not root.exists():
        return []
    try:
        return sorted(item.name for item in root.iterdir())[:50]
    except Exception as exc:  # noqa: BLE001
        logger.warning("ms_playwright_listing_failed path=%s error=%s", cache_path, exc)
        return []


def redis_connected() -> bool:
    if not settings.queue_enabled:
        return False
    try:
        redis_connection().ping()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("redis_connectivity_check_failed error=%s", exc)
        return False


def worker_running(*, redis_ok: bool | None = None) -> bool:
    if not settings.queue_enabled:
        return False
    if redis_ok is False:
        return False
    try:
        connection = redis_connection()
        return bool(connection.keys("rq:worker:*"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("rq_worker_status_check_failed error=%s", exc)
        return False
